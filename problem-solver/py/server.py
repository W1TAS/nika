import argparse
import signal
from threading import Event

from sc_client import client
from sc_kpm import ScServer
from modules.messageProcessingModule.MessageProcessingModule import MessageProcessingModule

from modules.animalKnowledgeModule.AnimalKnowledgeModule import AnimalKnowledgeModule

SC_SERVER_PROTOCOL = "protocol"
SC_SERVER_HOST = "host"
SC_SERVER_PORT = "port"

SC_SERVER_PROTOCOL_DEFAULT = "ws"
SC_SERVER_HOST_DEFAULT = "localhost"
SC_SERVER_PORT_DEFAULT = "8090"


def serve_until_disconnect(stop):
    """Выход при обрыве соединения запускает штатный restart контейнера.

    Новый процесс заново разрешает адреса узлов и подписывает агентов:
    после пересборки SC-памяти старые адреса и подписки недействительны.
    """
    while not stop.wait(2):
        if not client.is_connected():
            raise ConnectionError("Соединение с SC-памятью потеряно; требуется перезапуск агентов.")


def main(args: dict):
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    server = ScServer(
        f"{args[SC_SERVER_PROTOCOL]}://{args[SC_SERVER_HOST]}:{args[SC_SERVER_PORT]}")

    with server.connect():
        modules = [
            MessageProcessingModule(),
            AnimalKnowledgeModule()
        ]
        server.add_modules(*modules)
        with server.register_modules():
            serve_until_disconnect(stop)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--protocol', type=str, dest=SC_SERVER_PROTOCOL, default=SC_SERVER_PROTOCOL_DEFAULT, help="Sc-server protocol")
    parser.add_argument(
        '--host', type=str, dest=SC_SERVER_HOST, default=SC_SERVER_HOST_DEFAULT, help="Sc-server host")
    parser.add_argument(
        '--port', type=int, dest=SC_SERVER_PORT, default=SC_SERVER_PORT_DEFAULT, help="Sc-server port")
    args = parser.parse_args()

    main(vars(args))
