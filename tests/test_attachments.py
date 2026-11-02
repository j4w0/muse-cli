import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from muse_cli import attachments, cli


class ImageItemsTest(unittest.TestCase):
    def test_mime_from_bytes_not_extension_and_original_bytes_preserved(self):
        samples = [(b"\x89PNG\r\n\x1a\ncontents", "image/png"),
                   (b"\xff\xd8\xffcontents", "image/jpeg"),
                   (b"GIF89acontents", "image/gif"),
                   (b"GIF87acontents", "image/gif"),
                   (b"RIFF\x08\0\0\0WEBPcontents", "image/webp")]
        with tempfile.TemporaryDirectory() as tmp:
            for data, mime in samples:
                path = Path(tmp, "image with spaces.bin")
                path.write_bytes(data)
                item, = attachments.image_items([str(path)])
                self.assertEqual(item, {"type": "image", "mime_type": mime,
                                       "filename": path.name,
                                       "data_base64": base64.b64encode(data).decode("ascii")})

    def test_multiple_images_and_home_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "first.png").write_bytes(b"\x89PNG\r\n\x1a\nfirst")
            Path(tmp, "second.gif").write_bytes(b"GIF89asecond")
            with patch.dict(os.environ, {"HOME": tmp}):
                items = attachments.image_items(["~/first.png", "~/second.gif"])
            self.assertEqual([i["filename"] for i in items], ["first.png", "second.gif"])

    def test_missing_directory_empty_and_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in [("empty.png", b""), ("fake.png", b"text"),
                               ("unsafe.svg", b"<svg></svg>")]:
                Path(tmp, name).write_bytes(data)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    attachments.image_items([os.path.join(tmp, name)])
            for path in [tmp, os.path.join(tmp, "missing.png")]:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    attachments.image_items([path])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires named pipes")
    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pipe.png")
            os.mkfifo(path)
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                attachments.image_items([path])

    def test_read_error_is_actionable(self):
        with patch("os.stat", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(ValueError, "cannot read image.*Permission denied"):
                attachments.image_items(["private.png"])

    def test_combined_limit_and_exact_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "test.gif")
            path.write_bytes(b"GIF89a1234")
            with patch.object(attachments, "MAX_IMAGE_BYTES", 10):
                self.assertEqual(len(attachments.image_items([str(path)])), 1)
                with self.assertRaisesRegex(ValueError, "combined upload limit"):
                    attachments.image_items([str(path), str(path)])
                path.write_bytes(b"GIF89a12345")
                with self.assertRaisesRegex(ValueError, "combined upload limit"):
                    attachments.image_items([str(path)])


class SendImagesTest(unittest.TestCase):
    def send(self, argv):
        gw = MagicMock()
        gw._open.return_value = 8
        gw.call_json.return_value = {"chat_events": []}
        with patch.object(cli, "load_config", return_value={"node_id": "test-node"}), \
                patch.object(cli, "connect", return_value=gw), \
                patch.object(cli, "out") as out:
            cli.cmd_send(cli.build_parser().parse_args(["send", *argv]))
        return gw, out.call_args.args[0]

    def test_text_and_repeatable_image_with_thread_and_fire_and_forget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "screenshot.png")
            path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
            gw, result = self.send(["Review these", "--image", str(path), "--image", str(path),
                                    "--thread", "side", "--wait", "0"])
        method = gw._open.call_args.args[0]
        body = gw._open.call_args.kwargs["body"]
        self.assertEqual(method, "chat.stream")
        self.assertEqual(body["session_id"], "side")
        self.assertEqual(body["node_id"], "test-node")
        self.assertEqual([i["type"] for i in body["items"]], ["text", "image", "image"])
        self.assertEqual(body["items"][0]["text"], "Review these")
        self.assertEqual(body["items"][1]["mime_type"], "image/png")
        self.assertTrue(result["sent"])
        gw.close.assert_called_once()

    def test_image_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "photo.jpg")
            path.write_bytes(b"\xff\xd8\xffphoto")
            gw, result = self.send(["--image", str(path), "--wait", "0"])
        self.assertEqual([i["type"] for i in gw._open.call_args.kwargs["body"]["items"]], ["image"])
        self.assertTrue(result["sent"])

    def test_text_only_payload_unchanged(self):
        gw, _ = self.send(["Hello", "--wait", "0"])
        self.assertEqual(gw._open.call_args.kwargs["body"],
                         {"items": [{"type": "text", "text": "Hello"}],
                          "node_id": "test-node", "capabilities": {}})

    def test_image_send_waits_for_reply_in_the_selected_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "image.gif")
            path.write_bytes(b"GIF89aimage")
            args = cli.build_parser().parse_args(["send", "Review", "--image", str(path),
                                                  "--thread", "side", "--wait", "10"])
            gw = MagicMock()
            gw._open.return_value = 9
            reply = {"seq": 2, "event_name": "message.assistant", "payload": {
                "role": "assistant", "display_text": "I see the image", "reply_to_message_id": "",
                "status": "completed"}}
            gw.call_json.side_effect = [{"chat_events": [{"seq": 1}]}, {"chat_events": [reply]}]
            with patch.object(cli, "load_config", return_value={"node_id": "test"}), \
                    patch.object(cli, "connect", return_value=gw), patch.object(cli, "out") as out:
                cli.cmd_send(args)
            self.assertEqual(out.call_args.args[0]["reply"]["text"], "I see the image")
            self.assertEqual(gw.call_json.call_args.kwargs["body"]["session_id"], "side")

    def test_invalid_second_attachment_does_not_send_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid = Path(tmp, "valid.gif")
            valid.write_bytes(b"GIF89aimage")
            args = cli.build_parser().parse_args(["send", "Review", "--image", str(valid),
                                                  "--image", os.path.join(tmp, "missing.png")])
            with patch.object(cli, "connect") as connect, self.assertRaises(ValueError):
                cli.cmd_send(args)
            connect.assert_not_called()

    def test_empty_send_is_argument_error(self):
        for argv in [["send"], ["send", "  "]]:
            with self.subTest(argv=argv), patch.object(cli, "connect") as connect, \
                    patch("sys.stderr", new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(argv)
                self.assertEqual(ctx.exception.code, 2)
                self.assertIn("text or at least one --image", err.getvalue())
                connect.assert_not_called()

    def test_invalid_image_fails_before_auth_or_send_without_traceback(self):
        with patch.object(cli, "load_config") as config, \
                patch.object(cli, "connect") as connect, \
                patch.object(cli, "start_update_check", return_value=None), \
                patch.object(cli, "finish_update_check"), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["send", "--image", "/not-present/screenshot.png"])
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("cannot read image", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())
            config.assert_not_called()
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
