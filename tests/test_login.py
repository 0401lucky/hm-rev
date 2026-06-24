import unittest

from hm_api.login import parse_callback_request


class CallbackRequestParsingTest(unittest.TestCase):
    def test_parse_crlf_post_body(self) -> None:
        body = b"code=abc&tempToken=tok&siteId=1"
        request = (
            b"POST /callback HTTP/1.1\r\n"
            + f"Content-Length: {len(body)}".encode()
            + b"\r\n\r\n"
            + body
        )

        path, params = parse_callback_request(request)

        self.assertEqual(path, "/callback")
        self.assertEqual(params["code"], "abc")
        self.assertEqual(params["tempToken"], "tok")
        self.assertEqual(params["siteId"], "1")

    def test_parse_lf_post_body(self) -> None:
        body = b"code=abc&tempToken=tok&siteId=1"
        request = (
            b"POST /callback HTTP/1.1\n"
            + f"Content-Length: {len(body)}".encode()
            + b"\n\n"
            + body
        )

        path, params = parse_callback_request(request)

        self.assertEqual(path, "/callback")
        self.assertEqual(params["code"], "abc")
        self.assertEqual(params["tempToken"], "tok")
        self.assertEqual(params["siteId"], "1")


if __name__ == "__main__":
    unittest.main()
