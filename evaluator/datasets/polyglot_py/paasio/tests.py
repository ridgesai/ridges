import errno
import os
import unittest
import inspect
import io
from unittest.mock import ANY, call, NonCallableMagicMock, patch

from main import MeteredFile, MeteredSocket


ZEN = b"""Beautiful is better than ugly.
Explicit is better than implicit.
Simple is better than complex.
Complex is better than complicated.
Flat is better than nested.
Sparse is better than dense.
Readability counts.
Special cases aren't special enough to break the rules.
Although practicality beats purity.
Errors should never pass silently.
Unless explicitly silenced.
In the face of ambiguity, refuse the temptation to guess.
There should be one-- and preferably only one --obvious way to do it.
Although that way may not be obvious at first unless you're Dutch.
Now is better than never.
Although never is often better than *right* now.
If the implementation is hard to explain, it's a bad idea.
If the implementation is easy to explain, it may be a good idea.
Namespaces are one honking great idea -- let's do more of those!
"""


class MockException(Exception):
    pass


class MockFile(io.BytesIO):
    def __init__(self, *args, chunk=None, exception=None, **kwargs):
        super(MockFile, self).__init__(*args, **kwargs)
        self.__chunk = chunk
        self.__exception = exception

    def __exit__(self, exc_type, exc_val, exc_tb):
        ret = super(MockFile, self).__exit__(exc_type, exc_val, exc_tb)
        if exc_type is not None and "suppress" in exc_val.args[0]:
            return True
        return ret

    def read(self, size=-1):
        if self.__exception is not None:
            raise self.__exception
        if self.__chunk is None:
            return super(MockFile, self).read(size)
        if size is None:
            return super(MockFile, self).read(self.__chunk)
        if size < 0:
            return super(MockFile, self).read(self.__chunk)
        return super(MockFile, self).read(min(self.__chunk, size))

    def write(self, data):
        if self.__chunk is None:
            return super(MockFile, self).write(data)
        return super(MockFile, self).write(data[: self.__chunk])


class MockSock:
    def __init__(self, *, chunk=None, exception=None):
        self._recver = io.BytesIO(ZEN)
        self._sender = io.BytesIO()
        self.__closed = False
        self.__chunk = chunk
        self.__exception = exception
        self.flags = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._recver.close()
        self._sender.close()
        self.__closed = True
        if exc_type is not None and "suppress" in exc_val.args[0]:
            return True
        return False

    def recv(self, bufsize, flags=0):
        if self.__closed:
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        if bufsize is None:
            raise TypeError("'NoneType' object cannot be interpreted as an integer")
        if not isinstance(flags, int):
            raise TypeError(
                "an integer is required (got type {})".format(type(flags).__name__)
            )
        self.flags = flags
        if self.__exception is not None:
            raise self.__exception
        if self.__chunk is None:
            return self._recver.read(bufsize)
        else:
            return self._recver.read(min(self.__chunk, bufsize))

    def send(self, data, flags=0):
        if self.__closed:
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        if not isinstance(flags, int):
            raise TypeError(
                "an integer is required (got type {})".format(type(flags).__name__)
            )
        self.flags = flags
        if self.__chunk is None:
            return self._sender.write(data)
        return self._sender.write(data[: self.__chunk])


class SuperMock:
    """Mock for super().__init__ calls only, as mock.MagicMock cannot."""

    def __init__(self, *args, **kwargs):
        if self.initialized:
            self.init_called += 1
        else:
            self.initialized = True

    def __call__(self, *args, **kwargs):
        frame = inspect.currentframe()
        if frame is None:
            raise RuntimeError("Could not get current frame object")
        stack = inspect.getouterframes(frame)
        if any(frame[3] == "__init__" and "main" in frame[1] for frame in stack):
            return self
        else:
            return self.mock_object

    def __repr__(self):
        return "<SuperMock at {} with mock object: {!r}>".format(
            hex(id(self)), self.mock_object
        )

    mock_object = None
    init_called = 0
    initialized = False

class PaasioTest(unittest.TestCase):
    def test_meteredsocket_context_manager(self):
        wrapped = MockSock()
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        mock.__exit__.side_effect = wrapped.__exit__
        with MeteredSocket(mock) as socket:
            self.assertFalse(mock.__enter__.called)
            socket.recv(30)
        self.assertFalse(mock.__enter__.called)
        mock.__exit__.assert_called_once_with(None, None, None)
        self.assertEqual(2, len(mock.mock_calls))
        with self.assertRaisesRegex(OSError, os.strerror(errno.EBADF)):
            socket.recv(30)
        with self.assertRaisesRegex(OSError, os.strerror(errno.EBADF)):
            socket.send(b"")

    def test_meteredsocket_context_manager_exception_raise(self):
        exception = MockException("Should raise")
        wrapped = MockSock(exception=exception)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        mock.__exit__.side_effect = wrapped.__exit__
        with self.assertRaisesRegex(MockException, "Should raise") as err:
            with MeteredSocket(mock) as socket:
                self.assertFalse(mock.__enter__.called)
                socket.recv(4096)
        self.assertFalse(mock.__enter__.called)
        mock.__exit__.assert_called_once_with(
            MockException,
            err.exception,
            ANY,
        )
        self.assertEqual(exception, err.exception)

    def test_meteredsocket_context_manager_exception_suppress(self):
        exception = MockException("Should suppress")
        wrapped = MockSock(exception=exception)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        mock.__exit__.side_effect = wrapped.__exit__
        with MeteredSocket(mock) as socket:
            self.assertFalse(mock.__enter__.called)
            socket.recv(4096)
        self.assertFalse(mock.__enter__.called)
        mock.__exit__.assert_called_once_with(
            MockException,
            exception,
            ANY,
        )

    def test_meteredsocket_recv_once(self):
        mock = NonCallableMagicMock(wraps=MockSock(), autospec=True)
        with MeteredSocket(mock) as socket:
            actual_recv = socket.recv(4096)
        self.assertEqual(ZEN, actual_recv)
        self.assertEqual(1, socket.recv_ops)
        self.assertEqual(len(ZEN), socket.recv_bytes)
        self.assertEqual(1, mock.recv.call_count)

    def test_meteredsocket_recv_multiple(self):
        wrapped = MockSock()
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        actual_recv = b""
        with MeteredSocket(mock) as socket:
            for _ in range(5):
                actual_recv += socket.recv(30)
        self.assertEqual(ZEN[:150], actual_recv)
        self.assertEqual(5, socket.recv_ops)
        self.assertEqual(150, socket.recv_bytes)
        self.assertEqual(5, mock.recv.call_count)

    def test_meteredsocket_recv_multiple_chunk(self):
        wrapped = MockSock(chunk=20)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        actual_recv = b""
        with MeteredSocket(mock) as socket:
            for _ in range(5):
                actual_recv += socket.recv(4096)
            actual_recv += socket.recv(10)
        self.assertEqual(ZEN[:110], actual_recv)
        self.assertEqual(6, socket.recv_ops)
        self.assertEqual(110, socket.recv_bytes)
        self.assertEqual(6, mock.recv.call_count)

    def test_meteredsocket_recv_under_size(self):
        wrapped = MockSock(chunk=257)  # largish odd number
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        with MeteredSocket(mock) as socket:
            actual_recv = socket.recv(4096)
        self.assertEqual(ZEN[:257], actual_recv)
        self.assertEqual(1, socket.recv_ops)
        self.assertEqual(257, socket.recv_bytes)
        self.assertEqual(1, mock.recv.call_count)

    def test_meteredsocket_send_once(self):
        wrapped = MockSock(chunk=257)  # largish odd number
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        with MeteredSocket(mock) as socket:
            send_len = socket.send(ZEN)
            self.assertEqual(ZEN[:257], wrapped._sender.getbuffer())
        self.assertEqual(257, send_len)
        self.assertEqual(1, socket.send_ops)
        self.assertEqual(257, socket.send_bytes)
        self.assertEqual(1, mock.send.call_count)

    def test_meteredsocket_send_multiple(self):
        wrapped = MockSock()
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        send_len = 0
        expected = b"Tomorrow's victory is today's practice."
        with MeteredSocket(mock) as socket:
            send_len += socket.send(b"Tomorro")
            send_len += socket.send(b"w's victo")
            send_len += socket.send(b"ry is today")
            send_len += socket.send(b"'s practice.")
            self.assertEqual(expected, wrapped._sender.getbuffer())
        self.assertEqual(39, send_len)
        self.assertEqual(4, socket.send_ops)
        self.assertEqual(39, socket.send_bytes)
        self.assertEqual(4, mock.send.call_count)

    def test_meteredsocket_send_under_size(self):
        wrapped = MockSock(chunk=257)  # largish odd number
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        with MeteredSocket(mock) as socket:
            send_len = socket.send(ZEN[:123])
            self.assertEqual(ZEN[:123], wrapped._sender.getbuffer())
        self.assertEqual(123, send_len)
        self.assertEqual(1, socket.send_ops)
        self.assertEqual(123, socket.send_bytes)
        self.assertEqual(1, mock.send.call_count)

    def test_meteredsocket_bufsize_required(self):
        mock = NonCallableMagicMock(wraps=MockSock(), autospec=True)
        with self.assertRaisesRegex(TypeError, "argument"):
            with MeteredSocket(mock) as socket:
                socket.recv()
        self.assertFalse(mock.recv.called)

        mock = NonCallableMagicMock(wraps=MockSock(), autospec=True)
        with self.assertRaisesRegex(TypeError, "^'NoneType'.+integer$"):
            with MeteredSocket(mock) as socket:
                socket.recv(None)
        self.assertTrue(
            call(None) in mock.recv.mock_calls
            or call(None, ANY) in mock.recv.mock_calls
        )

    def test_meteredsocket_flags_support(self):
        mock = NonCallableMagicMock(wraps=MockSock(), autospec=True)
        with MeteredSocket(mock) as socket:
            self.assertEqual(len(ZEN), socket.send(ZEN, 42))
            self.assertEqual(ZEN, socket.recv(4096, 24))
        mock.send.assert_called_once_with(ZEN, 42)
        mock.recv.assert_called_once_with(4096, 24)

        wrapped = MockSock()
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        with MeteredSocket(mock) as socket:
            socket.recv(50)
            self.assertEqual(0, wrapped.flags)
            socket.send(b"no flags")
            self.assertEqual(0, wrapped.flags)
            socket.recv(30, 30)
            self.assertEqual(30, wrapped.flags)
            socket.send(b"flags", 1024)
            self.assertEqual(1024, wrapped.flags)
            with self.assertRaisesRegex(TypeError, "integer is required"):
                socket.send(b"data", None)
            with self.assertRaisesRegex(TypeError, "integer is required"):
                socket.send(b"data", b"flags")
            with self.assertRaisesRegex(TypeError, "integer is required"):
                socket.recv(b"data", None)
            with self.assertRaisesRegex(TypeError, "integer is required"):
                socket.recv(b"data", b"flags")

    def test_meteredsocket_stats_read_only(self):
        mock = NonCallableMagicMock(wraps=MockSock(), autospec=True)
        with MeteredSocket(mock) as socket:
            self.assertEqual(0, socket.send_ops)
            self.assertEqual(0, socket.send_bytes)
            self.assertEqual(0, socket.recv_ops)
            self.assertEqual(0, socket.recv_bytes)
            for _ in range(277):
                socket.send(b"b")
            socket.send(b"bytes")
            for _ in range(257):
                socket.recv(1)
            socket.recv(2)
            self.assertEqual(278, socket.send_ops)
            self.assertEqual(282, socket.send_bytes)
            self.assertEqual(258, socket.recv_ops)
            self.assertEqual(259, socket.recv_bytes)
            with self.assertRaises(AttributeError, msg="property 'send_ops' of 'MeteredSocket' object has no setter"):
                socket.send_ops = 0
            with self.assertRaises(AttributeError, msg="property 'send_bytes' of 'MeteredSocket' object has no setter"):
                socket.send_bytes = 0
            with self.assertRaises(AttributeError, msg="property 'recv_ops' of 'MeteredSocket' object has no setter"):
                socket.recv_ops = 0
            with self.assertRaises(AttributeError, msg="property 'recv_bytes' of 'MeteredSocket' object has no setter"):
                socket.recv_bytes = 0
            self.assertEqual(278, socket.send_ops)
            self.assertEqual(282, socket.send_bytes)
            self.assertEqual(258, socket.recv_ops)
            self.assertEqual(259, socket.recv_bytes)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_context_manager(self, super_mock):
        wrapped = MockFile(ZEN)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        mock.__exit__.side_effect = wrapped.__exit__
        super_mock.mock_object = mock
        with MeteredFile() as file:
            self.assertEqual(1, super_mock.init_called)
            self.assertFalse(mock.__enter__.called)
            file.read()
        self.assertFalse(mock.__enter__.called)
        mock.__exit__.assert_called_once_with(None, None, None)
        self.assertEqual(2, len(mock.mock_calls))
        with self.assertRaisesRegex(ValueError, "I/O operation on closed file."):
            file.read()
        with self.assertRaisesRegex(ValueError, "I/O operation on closed file."):
            file.write(b"data")

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_context_manager_exception_raise(self, super_mock):
        exception = MockException("Should raise")
        wrapped = MockFile(ZEN, exception=exception)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        mock.__exit__.side_effect = wrapped.__exit__
        super_mock.mock_object = mock
        with self.assertRaisesRegex(MockException, "Should raise") as err:
            with MeteredFile() as file:
                self.assertFalse(mock.__enter__.called)
                file.read()
        self.assertFalse(mock.__enter__.called)
        mock.__exit__.assert_called_once_with(
            MockException,
            err.exception,
            ANY,
        )
        self.assertEqual(exception, err.exception)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_context_manager_exception_suppress(self, super_mock):
        exception = MockException("Should suppress")
        wrapped = MockFile(ZEN, exception=exception)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        mock.__exit__.side_effect = wrapped.__exit__
        super_mock.mock_object = mock
        with MeteredFile() as file:
            self.assertFalse(mock.__enter__.called)
            file.read()
        self.assertFalse(mock.__enter__.called)
        mock.__exit__.assert_called_once_with(
            MockException,
            exception,
            ANY,
        )

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_iteration(self, super_mock):
        mock = NonCallableMagicMock(wraps=MockFile(ZEN), autospec=True)
        super_mock.mock_object = mock
        actual_reads = b""
        file = MeteredFile()
        for line in file:
            actual_reads += line
            self.assertLess(0, mock.readline.call_count, "File's readline not called")
            self.assertGreater(
                50, mock.readline.call_count, "Possible infinte loop detected"
            )
            self.assertEqual(file.read_ops, mock.readline.call_count)
        self.assertFalse(mock.__iter__.called)
        self.assertEqual(len(ZEN), file.read_bytes)
        self.assertEqual(ZEN, actual_reads)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_read_once(self, super_mock):
        mock = NonCallableMagicMock(wraps=MockFile(ZEN), autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            actual_read = file.read()
        self.assertEqual(ZEN, actual_read)
        self.assertEqual((len(ZEN)), file.read_bytes)
        self.assertEqual(1, file.read_ops)
        self.assertEqual(mock.read.call_count, file.read_ops)
        mock = NonCallableMagicMock(wraps=MockFile(ZEN), autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            actual_read = file.read(None)
        self.assertEqual(ZEN, actual_read)
        self.assertEqual((len(ZEN)), file.read_bytes)
        self.assertEqual(1, file.read_ops)
        self.assertEqual(mock.read.call_count, file.read_ops)
        mock = NonCallableMagicMock(wraps=MockFile(ZEN), autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            actual_read = file.read(-1)
        self.assertEqual(ZEN, actual_read)
        self.assertEqual((len(ZEN)), file.read_bytes)
        self.assertEqual(1, file.read_ops)
        self.assertEqual(mock.read.call_count, file.read_ops)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_read_multiple(self, super_mock):
        wrapped = MockFile(ZEN)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        actual_read = b""
        with MeteredFile() as file:
            for _ in range(5):
                actual_read += file.read(30)
        self.assertEqual(ZEN[:150], actual_read)
        self.assertEqual(5, file.read_ops)
        self.assertEqual(150, file.read_bytes)
        self.assertEqual(5, mock.read.call_count)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_read_multiple_chunk(self, super_mock):
        wrapped = MockFile(ZEN, chunk=20)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        actual_read = b""
        with MeteredFile() as file:
            for _ in range(5):
                actual_read += file.read()
            actual_read += file.read(10)
        self.assertEqual(ZEN[:110], actual_read)
        self.assertEqual(6, file.read_ops)
        self.assertEqual(110, file.read_bytes)
        self.assertEqual(6, mock.read.call_count)
        wrapped = MockFile(ZEN, chunk=20)
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        actual_read = b""
        with MeteredFile() as file:
            for size in [None, -2, -1, 0, 1, 2]:
                actual_read += file.read(size)
            actual_read += file.read(10)
        self.assertEqual(ZEN[:73], actual_read)
        self.assertEqual(7, file.read_ops)
        self.assertEqual(73, file.read_bytes)
        self.assertEqual(7, mock.read.call_count)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_read_under_size(self, super_mock):
        wrapped = MockFile(ZEN, chunk=257)  # largish odd number
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            actual_read = file.read()
        self.assertEqual(ZEN[:257], actual_read)
        self.assertEqual(1, file.read_ops)
        self.assertEqual(257, file.read_bytes)
        self.assertEqual(1, mock.read.call_count)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_write_once(self, super_mock):
        wrapped = MockFile(chunk=257)  # largish odd number
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            write_len = file.write(ZEN)
            self.assertEqual(ZEN[:257], wrapped.getbuffer())
        self.assertEqual(257, write_len)
        self.assertEqual(1, file.write_ops)
        self.assertEqual(257, file.write_bytes)
        self.assertEqual(1, mock.write.call_count)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_write_multiple(self, super_mock):
        wrapped = MockFile()
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        write_len = 0
        expected = b"Tomorrow's victory is today's practice."
        with MeteredFile() as file:
            write_len += file.write(b"Tomorro")
            write_len += file.write(b"w's victo")
            write_len += file.write(b"ry is today")
            write_len += file.write(b"'s practice.")
            self.assertEqual(expected, wrapped.getbuffer())
        self.assertEqual(39, write_len)
        self.assertEqual(4, file.write_ops)
        self.assertEqual(39, file.write_bytes)
        self.assertEqual(4, mock.write.call_count)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_write_under_size(self, super_mock):
        wrapped = MockFile(chunk=257)  # largish odd number
        mock = NonCallableMagicMock(wraps=wrapped, autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            write_len = file.write(ZEN[:123])
            self.assertEqual(ZEN[:123], wrapped.getbuffer())
        self.assertEqual(123, write_len)
        self.assertEqual(1, file.write_ops)
        self.assertEqual(123, file.write_bytes)
        self.assertEqual(1, mock.write.call_count)

    @patch("main.super", create=True, new_callable=SuperMock)
    def test_meteredfile_stats_read_only(self, super_mock):
        mock = NonCallableMagicMock(wraps=MockFile(ZEN), autospec=True)
        super_mock.mock_object = mock
        with MeteredFile() as file:
            self.assertEqual(0, file.read_ops)
            self.assertEqual(0, file.read_bytes)
            for _ in range(57):
                file.read(1)
            file.read(2)
            self.assertEqual(58, file.read_ops)
            self.assertEqual(59, file.read_bytes)
            self.assertEqual(0, file.write_ops)
            self.assertEqual(0, file.write_bytes)
            for _ in range(77):
                file.write(b"b")
            file.write(b"bytes")
            self.assertEqual(78, file.write_ops)
            self.assertEqual(82, file.write_bytes)
            with self.assertRaises(AttributeError, msg="property 'write_ops' of 'MeteredFile' object has no setter"):
                file.write_ops = 0
            with self.assertRaises(AttributeError, msg="property 'write_bytes' of 'MeteredFile' object has no setter"):
                file.write_bytes = 0
            with self.assertRaises(AttributeError, msg="property 'read_ops' of 'MeteredFile' object has no setter"):
                file.read_ops = 0
            with self.assertRaises(AttributeError, msg="property 'read_bytes' of 'MeteredFile' object has no setter"):
                file.read_bytes = 0
            self.assertEqual(78, file.write_ops)
            self.assertEqual(82, file.write_bytes)
            self.assertEqual(58, file.read_ops)
            self.assertEqual(59, file.read_bytes)
