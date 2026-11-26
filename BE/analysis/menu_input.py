"""One menu embedding input shared by preparation and vector lookup.

Description-free names keep their old lookup keys. Described menus have a key
for the (name, description) pair, so identically named courses do not collapse.
Only source text is included; store/category/price labels are not inferred.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class MenuInput:
    key: str
    name: str
    description: str
    text: str


def menu_input(record: dict) -> MenuInput:
    value = record.get("menu_name")
    name = value.strip() if isinstance(value, str) else ""
    value = record.get("description")
    description = " ".join(value.split()) if isinstance(value, str) else ""
    if not description:
        return MenuInput(name, name, "", name)
    identity = json.dumps([name, description], ensure_ascii=False, separators=(",", ":"))
    key = "menu:v2:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return MenuInput(key, name, description, name + "\n" + description)
