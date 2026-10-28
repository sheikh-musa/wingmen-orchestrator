"""peer_uid — the AF_UNIX caller identity, cross-platform.

Plan §3 correction: irsyad's SO_PEERCRED is Linux-only; THIS daemon runs on
the Mac Mini, so the macOS path is LOCAL_PEERCRED (struct xucred: cr_version
then cr_uid). Try SO_PEERCRED first (Linux), fall back to LOCAL_PEERCRED
(macOS), raise on neither — the server maps any failure to fail-closed
INTERNAL_ERROR, never "allow".
"""
import socket
import sys
import tempfile
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import peer  # noqa: E402


def _real_unix_conn():
    """Listener + client over a SHALLOW tmp path (macOS sun_path cap is
    104 bytes — a deep mkdtemp blows it)."""
    with tempfile.TemporaryDirectory(prefix="provpeer-",
                                     dir=tempfile.gettempdir()) as td:
        path = str(Path(td) / "s.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cli.connect(path)
        conn, _ = srv.accept()
        try:
            yield conn, cli
        finally:
            conn.close()
            cli.close()
            srv.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="LOCAL_PEERCRED path is macOS")
def test_peer_uid_resolves_on_macos_local_peercred():
    for conn, _cli in _real_unix_conn():
        uid = peer.peer_uid(conn)
        import os
        assert uid == os.getuid()  # we connected as ourselves


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED path is Linux")
def test_peer_uid_resolves_on_linux_so_peercred():
    for conn, _cli in _real_unix_conn():
        import os
        assert peer.peer_uid(conn) == os.getuid()


def test_xucred_unpack_reads_uid_after_version():
    # struct xucred: u_int cr_version; uid_t cr_uid; ... — uid is the SECOND
    # u32. A buffer that looks like version=0, uid=501 must yield 501.
    import struct
    assert peer._unpack_xucred(struct.pack("3I", 0, 501, 16)) == 501


def test_peer_uid_fails_closed_when_no_mechanism(monkeypatch):
    # neither getsockopt path exists -> raise (server maps to INTERNAL_ERROR)
    class NoCredSocket:
        def getsockopt(self, *a):
            raise OSError("no such option")
    with pytest.raises(OSError):
        peer.peer_uid(NoCredSocket())


def test_peer_uid_prefers_so_peercred_when_present(monkeypatch):
    # on a platform where SO_PEERCRED exists, it wins (Linux order)
    import struct
    monkeypatch.setattr(peer.socket, "SO_PEERCRED", 9998, raising=False)

    class FakeSock:
        def getsockopt(self, level, opt, size):
            assert opt == 9998
            return struct.pack("3i", 123, 4242, 4242)

    assert peer.peer_uid(FakeSock()) == 4242
