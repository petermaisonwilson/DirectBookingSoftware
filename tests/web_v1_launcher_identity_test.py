from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online import BUILD
from run_online import port_is_available


def main() -> None:
    assert BUILD == '279'

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        assert port_is_available('127.0.0.1', port) is False

    assert port_is_available('127.0.0.1', port) is True
    print('Direct Booking Web V1 launcher identity test: passed')


if __name__ == '__main__':
    main()
