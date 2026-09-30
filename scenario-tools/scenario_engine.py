#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Механизм ручного (не обученного) сценарного разбора сообщений для NIKA.
ЛР №2 по курсу ТиИСПИС, предметная область "Животный мир".

ПОЧЕМУ ТАК. Wit.ai не обучает intents для новых приложений — это
подтверждённый баг платформы (wit-ai/wit issue #2851): GET /intents,
/entities, /utterances отдают загруженные данные корректно, но
GET /message всегда возвращает "intents": [] даже на фразу, дословно
совпадающую с обучающей. Поэтому вместо того, чтобы полагаться на
статистическую модель Wit.ai, классы сообщений и сущности здесь
ПРОПИСАНЫ ВРУЧНУЮ: явные правила (регулярные выражения) на класс
сообщения и явный справочник сущностей из базы знаний. Никакого
обучения, подбора весов или похожести текстов здесь нет — это прямое
соответствие "фраза -> класс сообщения" и "слово -> сущность",
которое разработчик задаёт сам, как и предполагает п. 3.2 практикума
(множество распознаваемых сообщений формализуется в самой базе знаний).

Модуль:
  1) читает kb/.../concepts/instances/*.scs и messages/*.scs и строит
     справочник сущностей (concept_wit_entity) с их характеристиками
     (nrel_note, nrel_habitat, nrel_food_type, nrel_conservation_status,
     nrel_listed_in_red_book, nrel_species);
  2) для каждого класса сообщений (concept_message_about_*) хранит
     список вручную заданных правил (регулярных выражений), полностью
     совпадающих с шаблонами фраз, по которым сгенерирована обучающая
     выборка для Wit.ai (tools/export_wit_app.py) — то есть работает
     на тех же вопросах, но без обращения к Wit.ai;
  3) формирует ответное сообщение по данным базы знаний — так же,
     как это делают логические правила ЛР №3 (nrel_habitat -> текст
     "X обитает в Y" и т. д.), только пока в виде явных шаблонов,
     а не продукций predicate.scs.

Это НЕ статистический классификатор (в отличие от tools/wit_mock_server.py,
который остаётся в проекте как вспомогательный HTTP-адаптер под интерфейс
Wit.ai). Это основной, документированный в отчёте способ выполнить ЛР №2
без зависимости от сломанного облачного сервиса.

ЗАПУСК:
    python3 tools/scenario_engine.py --test     # прогон сценария диалога
    python3 tools/scenario_engine.py --ask "Где обитает зубр?"
"""

import argparse
import os
import re
import sys

KB = "kb/extra/section_subject_domain_of_animal_world"
INSTANCES_DIR = os.path.join(KB, "concepts", "instances")
MESSAGES_DIR = os.path.join(KB, "messages")

IDTF_RU = re.compile(r"=> nrel_main_idtf:\s*\[([^\]]+)\]\s*\(\*\s*<-\s*lang_ru", re.S)
HEAD = re.compile(r"^([a-z_][a-z0-9_]*)\s*\n", re.M)
CLASSES = re.compile(r"^\s*<-\s*([a-z_][a-z0-9_]*)\s*;", re.M)
FIELD = re.compile(r"=>\s*(nrel_[a-z_]+)\s*:\s*([a-z_][a-z0-9_]*)\s*;")
SCIENTIFIC_NAME = re.compile(r"=>\s*nrel_scientific_name:\s*\[([^\]]+)\]\s*\(\*\s*<-\s*lang_en", re.S)

FALLBACK_MESSAGE = "Не могу ответить на этот вопрос: в базе знаний нет необходимых сведений."

# ---------------------------------------------------------------------
# 1) Справочник сущностей: разбор .scs файлов экземпляров.
# ---------------------------------------------------------------------

def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_entities(root="."):
    """id -> {ru, classes:set, note, habitat, food_type,
              conservation_status, listed_in_red_book:list, species}"""
    entities = {}
    idir = os.path.join(root, INSTANCES_DIR)
    for name in sorted(os.listdir(idir)):
        if not name.endswith(".scs"):
            continue
        text = _read(os.path.join(idir, name))
        # файл может содержать несколько узлов; разбиваем по пустой строке
        # перед идентификатором в начале строки
        heads = list(HEAD.finditer(text))
        for i, m in enumerate(heads):
            sid = m.group(1)
            start = m.start()
            # верхняя граница блока — начало следующего узла (или конец файла)
            upper = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            # конец самого узла — последнее ";;" перед этой границей (последнее,
            # а не первое, т.к. внутри блока встречаются внутренние "(* ... ;; *)")
            tail = text.rfind(";;", start, upper)
            end = upper if tail == -1 else tail + 2
            block = text[start:end]
            classes = set(CLASSES.findall(block))
            if "concept_wit_entity" not in classes:
                continue
            ru = None
            idtf = IDTF_RU.search(block)
            if idtf:
                ru = idtf.group(1).strip()
            fields = {}
            for rel, val in FIELD.findall(block):
                fields.setdefault(rel, []).append(val)
            scientific_m = SCIENTIFIC_NAME.search(block)
            scientific_name = scientific_m.group(1).strip() if scientific_m else None

            entities[sid] = {
                "ru": ru or sid,
                "classes": classes,
                "note": None,
                "fields": fields,
                "scientific_name": scientific_name,
            }
            note_m = re.search(
                r"=>\s*nrel_note:\s*\[([^\]]+)\]\s*\(\*\s*<-\s*lang_ru", block, re.S)
            if note_m:
                entities[sid]["note"] = note_m.group(1).strip()
    return entities


def name_of(entities, sid):
    return entities.get(sid, {}).get("ru", sid)


# ---------------------------------------------------------------------
# 2) Вручную заданные правила распознавания класса сообщения.
#    Порядок важен: первое совпавшее правило и определяет класс.
# ---------------------------------------------------------------------

RULES = [
    ("about_conservation", [
        r"красн\w*\s+книг\w*",
        r"охранн\w*\s+статус",
        r"занес[её]н\w*\s+ли",
        r"исчеза\w*\s+вид",
    ]),
    ("about_scientific_name", [
        r"научн\w*\s+назван\w*",
        r"по[- ]научн\w*",
        r"латинск\w*\s+назван\w*",
    ]),
    ("about_habitat", [
        r"где\s+обита\w*",
        r"где\s+жив[её]т",
        r"в\s+как\w*\s+сред\w*\s+обита\w*",
    ]),
    ("about_food", [
        r"чем\s+пита\w*",
        r"что\s+ест\b",
        r"чем\s+кормит\w*",
        r"на\s+кого\s+охот\w*",
    ]),
    ("about_behaviour", [
        r"как\s+вед[её]т\s+себя",
        r"поведен\w*",
        r"как\s+повед\w*",
    ]),
    ("about_reproduction", [
        r"как\s+размножа\w*",
        r"тип\w*\s+размножен\w*",
        r"способ\w*\s+размножен\w*",
    ]),
    ("about_decomposition", [
        r"каким\s+быва\w*",
        r"на\s+что\s+декомпозиру\w*",
        r"как\w*\s+вид\w*\s+бывают",
        r"раз\w*вид\w*",
    ]),
    ("about_entity", [
        r"что\s+так\w*\s+",
        r"расскаж\w*,?\s+что\s+так\w*",
        r"что\s+знач\w*",
        r"кто\s+так\w*",
    ]),
]

INTENT_CLASS = {
    "about_entity": "concept_message_about_entity",
    "about_scientific_name": "concept_message_about_scientific_name",
    "about_decomposition": "concept_message_about_decomposition",
    "about_habitat": "concept_message_about_habitat",
    "about_food": "concept_message_about_food",
    "about_behaviour": "concept_message_about_behaviour",
    "about_reproduction": "concept_message_about_reproduction",
    "about_conservation": "concept_message_about_conservation",
}


def classify(text):
    low = text.lower().replace("ё", "е")
    for intent, patterns in RULES:
        for p in patterns:
            if re.search(p.replace("ё", "е"), low):
                return intent
    return None


# ---------------------------------------------------------------------
# 3) Явное выделение сущности: точное вхождение известного имени
#    (nrel_main_idtf на русском) в текст сообщения, самое длинное
#    совпадение побеждает.
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Нормализация словоформ для поиска сущности.
#
# NIKA передаёт нам русское имя сущности из nrel_main_idtf в именительном
# падеже, а пользователь может поставить это имя в родительный,
# винительный, дательный, творительный или предложный падеж. Полноценного
# морфологического анализатора в этом локальном классификаторе нет, поэтому
# используем осторожную эвристику: строим набор основ слова, отбрасывая
# типичные русские падежные/родовые окончания. Например:
#   зубр  -> зубр
#   зубра -> зубр
#   зубру -> зубр
#   бурый -> бур
#   бурого -> бур
#   медведь -> медвед
#   медведя -> медвед
#
# Точное совпадение проверяется первым и имеет приоритет над эвристикой.
# ---------------------------------------------------------------------
_CASE_ENDINGS = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
    "ого", "его", "ыми", "ими", "ая", "яя", "ое", "ее",
    "ые", "ие", "ых", "их", "ым", "им", "ый", "ий",
    "ою", "ею", "ую", "юю", "ом", "ем", "ов", "ев",
    "ам", "ям", "ах", "ях", "ой", "ей",
    "а", "я", "у", "ю", "ы", "и", "е", "о", "ь",
)


def _word_stems(word):
    """Возвращает небольшой набор безопасных эвристических основ слова."""
    w = word.lower().replace("ё", "е")
    stems = {w}
    # Сначала более длинные окончания, чтобы не обрезать их по частям.
    for ending in sorted(_CASE_ENDINGS, key=len, reverse=True):
        if len(w) > len(ending) + 2 and w.endswith(ending):
            stems.add(w[:-len(ending)])
    # Мягкий знак часто исчезает в косвенных падежах:
    # медведь -> медведя, гость -> гостя.
    if w.endswith("ь") and len(w) > 3:
        stems.add(w[:-1])
    return {s for s in stems if len(s) >= 2}


def _tokenize_ru(text):
    return re.findall(r"[а-яеё]+(?:-[а-яеё]+)*", text.lower().replace("ё", "е"))


def _display_entity_name(name):
    """Имя, которым пользователь обычно называет объект, без тех. уточнения «(вид)»."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


def _entity_priority(data):
    """При неоднозначности предпочитаем экземпляр животного, а не таксон."""
    classes = data.get("classes", set())
    priority = 0
    if "concept_animal" in classes:
        priority += 40
    if "concept_biological_species" in classes:
        priority += 20
    if "concept_wit_entity" in classes:
        priority += 5
    if "concept_taxon" in classes and "concept_animal" not in classes:
        priority -= 5
    return priority


def _consonant_skeleton(word):
    """Грубый fallback для чередований вроде лев -> льва, пёс -> пса."""
    return re.sub(r"[аеёиоуыэюяьъ]", "", word.lower().replace("ё", "е"))


def find_entity(text, entities):
    low = text.lower().replace("ё", "е")

    # Для сопоставления используем пользовательское имя сущности без
    # технического уточнения «(вид)». Саму строку в KB при этом не меняем.
    candidates = []
    for sid, data in entities.items():
        ru = (data["ru"] or "").lower().replace("ё", "е").strip()
        display = _display_entity_name(ru)
        if display:
            candidates.append((sid, ru, display, data))

    # 1) Точное совпадение. Если одновременно есть «лев» и «лев (вид)»,
    #    берём пользовательский экземпляр животного, а не таксон.
    exact = []
    for sid, ru, display, data in candidates:
        if re.search(r"(?<![а-яё])" + re.escape(display) + r"(?![а-яё])", low):
            exact.append((
                _entity_priority(data),
                len(_tokenize_ru(display)),
                len(display),
                sid,
            ))
    if exact:
        exact.sort(reverse=True)
        return exact[0][3]

    # 2) Сопоставление по основам слов для обычных косвенных падежей.
    #    Например: зубр/зубра, бурый/бурого, медведь/медведя.
    query_tokens = _tokenize_ru(low)
    best = None
    best_score = None

    for sid, ru, display, data in candidates:
        name_tokens = _tokenize_ru(display)
        n = len(name_tokens)
        if not n or n > len(query_tokens):
            continue

        name_stems = [_word_stems(tok) for tok in name_tokens]
        for i in range(len(query_tokens) - n + 1):
            score = 0
            ok = True
            exact_words = 0
            for j in range(n):
                qword = query_tokens[i + j]
                if name_tokens[j] == qword:
                    exact_words += 1
                q_stems = _word_stems(qword)
                if name_stems[j] & q_stems:
                    score += 10
                    continue
                # Если окончание не спасло, пробуем согласный каркас.
                if _consonant_skeleton(name_tokens[j]) == _consonant_skeleton(qword):
                    score += 5
                    continue
                ok = False
                break

            if not ok:
                continue

            rank = (
                score,
                _entity_priority(data),
                exact_words,
                n,
                len(display),
            )
            if best_score is None or rank > best_score:
                best_score = rank
                best = sid

    return best


# ---------------------------------------------------------------------
# 4) Формирование ответа по данным базы знаний (шаблон на класс).
# ---------------------------------------------------------------------

def answer(intent, sid, entities):
    data = entities.get(sid)
    if not data:
        return FALLBACK_MESSAGE
    name = data["ru"]
    fields = data["fields"]

    if intent == "about_entity":
        note = data.get("note")
        return note if note else FALLBACK_MESSAGE

    if intent == "about_scientific_name":
        scientific_name = data.get("scientific_name")
        if not scientific_name:
            species_ids = fields.get("nrel_species", [])
            for spid in species_ids:
                sdata = entities.get(spid, {})
                scientific_name = sdata.get("scientific_name")
                if scientific_name:
                    break
        if not scientific_name:
            return FALLBACK_MESSAGE
        return "Научное название вида «%s» — %s." % (name, scientific_name)

    if intent == "about_habitat":
        hab = fields.get("nrel_habitat")
        if not hab:
            return FALLBACK_MESSAGE
        return "%s обитает в: %s." % (
            name.capitalize(), ", ".join(name_of(entities, h) for h in hab))

    if intent == "about_food":
        ft = fields.get("nrel_food_type")
        if not ft:
            return FALLBACK_MESSAGE
        return "Тип питания «%s» — %s." % (
            name, ", ".join(name_of(entities, f) for f in ft))

    if intent == "about_behaviour":
        behaviours = fields.get("nrel_behaviour")
        if not behaviours:
            return FALLBACK_MESSAGE
        return "Для «%s» характерно: %s." % (
            name, ", ".join(name_of(entities, b) for b in behaviours))

    if intent == "about_reproduction":
        types = fields.get("nrel_reproduction_type")
        if not types:
            return FALLBACK_MESSAGE
        return "Тип размножения «%s» — %s." % (
            name, ", ".join(name_of(entities, t) for t in types))

    if intent == "about_conservation":
        species_ids = fields.get("nrel_species", [sid])
        parts = []
        for spid in species_ids:
            sdata = entities.get(spid, {})
            sfields = sdata.get("fields", {})
            books = sfields.get("nrel_listed_in_red_book")
            status = sfields.get("nrel_conservation_status")
            if books:
                parts.append("вид %s занесён в: %s%s." % (
                    name_of(entities, spid),
                    ", ".join(name_of(entities, b) for b in books),
                    (" (охранный статус: %s)" % name_of(entities, status[0])
                     if status else "")))
        if parts:
            return "Да. " + " ".join(parts)
        return "«%s» не значится занесённым в Красную книгу по данным базы знаний." % name

    if intent == "about_decomposition":
        return ("Класс «%s» и его виды раскрываются в базе знаний через "
                "отношение nrel_subdividing (см. соответствующий .scs)." % name)

    return FALLBACK_MESSAGE


# ---------------------------------------------------------------------
# 5) Обработка одного сообщения целиком (то, что вызывает агент NIKA
#    вместо запроса к Wit.ai /message).
# ---------------------------------------------------------------------

def process(text, entities):
    intent = classify(text)
    sid = find_entity(text, entities)
    if intent and sid:
        reply = answer(intent, sid, entities)
    else:
        # Любой случай, когда сценарий не может дать осмысленный ответ:
        # неизвестный класс, неизвестная сущность или неполные данные в БЗ.
        reply = FALLBACK_MESSAGE
    return intent, sid, reply


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

SCENARIO = [
    "Что такое зубр?",
    "Где обитает зубр?",
    "Чем питается волк?",
    "Занесён ли зубр в Красную книгу?",
    "Как по-научному называется волк?",
    "Как ведёт себя бурый медведь?",
    "Как размножается белый аист?",
]

OUT_OF_DOMAIN = [
    "Привет!",
    "Какая погода в Минске?",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--ask", help="классифицировать одну фразу и выйти")
    ap.add_argument("--test", action="store_true",
                     help="прогнать сценарий диалога (4 вопроса) и контрольные "
                          "фразы вне предметной области")
    args = ap.parse_args()

    entities = load_entities(args.root)

    if args.ask:
        intent, sid, reply = process(args.ask, entities)
        print("Класс:    %s (%s)" % (intent or "—", INTENT_CLASS.get(intent, "—")))
        print("Сущность: %s" % (name_of(entities, sid) if sid else "—"))
        print("Ответ:    %s" % (reply or "нет ответа"))
        return 0

    print("Загружено сущностей (concept_wit_entity): %d\n" % len(entities))

    print("=== Сценарий диалога (dialogue_about_animal_world.scs) ===")
    for q in SCENARIO:
        intent, sid, reply = process(q, entities)
        print("\n> %s" % q)
        print("  класс:    %-22s сущность: %s" % (
            intent or "—", name_of(entities, sid) if sid else "—"))
        print("  ответ:    %s" % reply)

    print("\n=== Фразы вне предметной области (должны остаться без класса) ===")
    for q in OUT_OF_DOMAIN:
        intent, sid, reply = process(q, entities)
        print("  %-30s -> класс: %s" % (q, intent or "—"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
