"""Optional pytest plugin: fail closed on non-loopback sockets for this review."""
import socket


def pytest_configure(config):
    original=socket.socket.connect
    original_ex=socket.socket.connect_ex
    def check(address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost','testserver'):
            raise AssertionError('Non-loopback network forbidden during resource acceptance')
    def connect(sock,address):check(address);return original(sock,address)
    def connect_ex(sock,address):check(address);return original_ex(sock,address)
    socket.socket.connect=connect
    socket.socket.connect_ex=connect_ex
