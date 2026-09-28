"""Leitura restrita dos modelos Excel de cadastro do apontamento de campo."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
HEADERS = {
    "employees": ("Nome", "Função", "Equipe", "Ativo"),
    "services": ("Nome", "Categoria", "Unidade", "Exigir quantidade", "Ativo"),
}
COL_RE = re.compile(r"[A-Z]+")


def _column(ref):
    letters = COL_RE.match(ref or "")
    if not letters:
        raise ValueError("Célula inválida na planilha")
    value = 0
    for letter in letters.group():
        value = value * 26 + ord(letter) - 64
    return value - 1


def _boolean(value, row_number, field, default=True):
    normalized = str(value).strip().casefold()
    if not normalized:
        return default
    if normalized in ("sim", "s", "1", "true", "verdadeiro"):
        return True
    if normalized in ("não", "nao", "n", "0", "false", "falso"):
        return False
    raise ValueError(f"Linha {row_number}: {field} deve ser Sim ou Não")


def parse_catalog_xlsx(content: bytes, kind: str) -> list[dict]:
    if kind not in HEADERS:
        raise ValueError("Tipo de cadastro inválido")
    if not content.startswith(b"PK") or len(content) > 4 * 1024 * 1024:
        raise ValueError("Envie uma planilha .xlsx de até 4 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > 200 or sum(item.file_size for item in infos) > 16 * 1024 * 1024:
                raise ValueError("Planilha excede o limite de leitura")
            names = {item.filename for item in infos}
            if "xl/worksheets/sheet1.xml" not in names:
                raise ValueError("A primeira aba da planilha não foi encontrada")
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root.findall("m:si", NS)]
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError) as exc:
        raise ValueError("Arquivo Excel inválido ou corrompido") from exc

    table = []
    for row in root.findall(".//m:sheetData/m:row", NS):
        number = int(row.attrib.get("r", len(table) + 1))
        if number > 2002:
            raise ValueError("O modelo aceita até 2.000 registros")
        values = [""] * len(HEADERS[kind])
        for cell in row.findall("m:c", NS):
            col = _column(cell.attrib.get("r", ""))
            if col >= len(values):
                continue
            if cell.find("m:f", NS) is not None:
                raise ValueError(f"Linha {number}: fórmulas não são permitidas")
            raw = cell.find("m:v", NS)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                inline = cell.find("m:is", NS)
                value = "".join(inline.itertext()) if inline is not None else ""
            elif cell_type == "s" and raw is not None:
                value = shared[int(raw.text)]
            else:
                value = raw.text if raw is not None else ""
            values[col] = str(value or "").strip()
        table.append((number, values))
    if not table or tuple(table[0][1]) != HEADERS[kind]:
        raise ValueError("Cabeçalhos diferentes do modelo. Baixe o modelo desta tela e não altere a primeira linha")

    result = []
    for number, cells in table[1:]:
        if not any(cells):
            continue
        name = cells[0].strip()
        if not name or len(name) > 160:
            raise ValueError(f"Linha {number}: informe um nome de até 160 caracteres")
        if any(len(value) > 160 for value in cells):
            raise ValueError(f"Linha {number}: texto excede 160 caracteres")
        if kind == "employees":
            result.append({"name": name, "role": cells[1], "team": cells[2],
                           "active": _boolean(cells[3], number, "Ativo")})
        else:
            result.append({"name": name, "category": cells[1], "unit": cells[2] or "un.",
                           "requiresQuantity": _boolean(cells[3], number, "Exigir quantidade", False),
                           "active": _boolean(cells[4], number, "Ativo")})
    if not result:
        raise ValueError("A planilha não contém registros para importar")
    if len(result) > 2000:
        raise ValueError("O modelo aceita até 2.000 registros")
    return result
