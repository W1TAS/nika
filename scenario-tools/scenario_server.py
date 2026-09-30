#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP-обёртка над tools/scenario_engine.py в формате ответа Wit.ai /message.

Нужна только затем, что агент классификации NIKA обращается к классификатору
по HTTP (адрес задан в nika.ini как [wit-ai] url). Сама классификация здесь
НЕ статистическая: она выполняется вручную заданными правилами из
scenario_engine.py (явные регулярные выражения на класс сообщения + прямой
справочник сущностей из базы знаний) — этот файл только переводит результат
в JSON нужной формы, чтобы агенту не пришлось знать, что Wit.ai за ним нет.

ЗАПУСК:
    python3 tools/scenario_server.py            # 127.0.0.1:8080

В nika/nika.ini:
    [wit-ai]
    server_token = local
    url = http://127.0.0.1:8080/message

ПРОВЕРКА:
    curl 'http://127.0.0.1:8080/message?v=20240304&q=Где+обитает+зубр?'
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import scenario_engine as se

ENTITY_NAME = "rrel_entity:rrel_entity"


class Handler(BaseHTTPRequestHandler):
    entities = None

    def _answer(self, q):
        intent, sid, reply = se.process(q, self.entities)

        # Важно: `nika_reply` ниже является только отладочным полем.
        # StandardMessageReplyAgent его не использует как готовый текст ответа.
        # Если сценарный классификатор не нашёл сущность, не передаём
        # распознанный intent в NIKA: иначе NIKA пытается применить
        # специализированное lr_message_about_* правило, но его условие
        # с сущностью не выполняется и ответ вообще не генерируется.
        # Для такого случая оставляем сообщение без intent, чтобы сработало
        # штатное правило lr_not_classified_by_intent_message.
        # Недостаточно просто проверить sid: сущность может существовать в БЗ,
        # но не иметь данных, нужных именно для этого типа вопроса.
        # Например, «океан» распознаётся как сущность, однако у него нет
        # nrel_food_type, поэтому для «что ест океан» scenario_engine уже
        # возвращает FALLBACK_MESSAGE. В таком случае не передаём в NIKA ни
        # intent, ни entity — пусть сработает общий SC-fallback.
        fallback_message = getattr(
            se,
            "FALLBACK_MESSAGE",
            "Не могу ответить на этот вопрос: в базе знаний нет необходимых сведений.",
        )
        if not sid or reply == fallback_message:
            intent = None
            sid = None
            reply = None

        entities = {}
        if sid:
            name = se.name_of(self.entities, sid)
            start = q.lower().find(name.lower())
            # ВАЖНО: NIKA сопоставляет поле «value» с nrel_main_idtf (lang_ru)
            # каждого элемента concept_wit_entity. Поэтому value ДОЛЖЕН содержать
            # русское имя сущности (напр. «зубр»), а НЕ sc-идентификатор
            # (напр. «european_bison»). Если передать sc-идентификатор, агент
            # классификации не найдёт совпадения и создаст фиктивный sc-link,
            # из-за чего логические правила (lr_message_about_*) не сработают.
            entities[ENTITY_NAME] = [{
                "id": "local", "name": "rrel_entity", "role": "rrel_entity",
                "start": max(start, 0), "end": max(start, 0) + len(name),
                "body": name, "confidence": 1, "entities": [],
                "value": name, "type": "value",
            }]
        intents = [{"id": "local", "name": intent, "confidence": 1.0}] if intent else []
        payload = {"text": q, "intents": intents, "entities": entities, "traits": {}}
        if reply:
            payload["nika_reply"] = reply  # для отладки; сам ответ формируют правила ЛР №3

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        sys.stderr.write("  %-45s -> %-22s %s\n" % (q[:45], intent or "—", reply or "—"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.endswith("/message"):
            self.send_error(404)
            return
        self._answer(parse_qs(parsed.query).get("q", [""])[0])

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        if not q and raw:
            try:
                obj = json.loads(raw)
                q = obj.get("q") or obj.get("text") or ""
            except ValueError:
                q = raw
        self._answer(q)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    Handler.entities = se.load_entities(args.root)
    print("Загружено сущностей: %d" % len(Handler.entities))
    srv = HTTPServer((args.host, args.port), Handler)
    print("Сценарный классификатор слушает http://%s:%d/message" % (args.host, args.port))
    print("В nika.ini:  url = http://%s:%d/message\n" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
