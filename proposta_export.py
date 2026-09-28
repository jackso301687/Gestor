"""Geração XLSX/PDF da proposta a partir do modelo oficial entregue."""
from __future__ import annotations

import copy
import base64
import html
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "Proposta de medição" / "Proposta_de_medição MODELO.xlsx"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
ET.register_namespace("", MAIN); ET.register_namespace("r", REL)
GRID_FIRST_ROW = 6
GRID_LAST_ROW = 53
GRID_CAPACITY = GRID_LAST_ROW - GRID_FIRST_ROW + 1
SIGNATURE_ROW_1 = 57
SIGNATURE_ROW_2 = 61
SIGNATURE_LAST_ROW = SIGNATURE_ROW_2
GRID_ROW_HEIGHT = "12"


def _q(tag): return f"{{{MAIN}}}{tag}"
def _rq(tag): return f"{{{REL}}}{tag}"
def _pq(tag): return f"{{{PKG}}}{tag}"
def _cq(tag): return f"{{{CT}}}{tag}"


def _lines(proposal):
    rows = []
    for service in proposal["servicos"]:
        items = service["itens"] or [{"bloco": "", "casa": ""}]
        for item in items:
            rows.append({"servico": service["nome_servico"], "bloco": item.get("bloco") or "", "casa": item.get("casa") or ""})
    return rows or [{"servico": "", "bloco": "", "casa": ""}]


def _pages(rows):
    return [rows[index:index + GRID_CAPACITY] for index in range(0, len(rows), GRID_CAPACITY)]


def _cell(sheet, reference):
    for cell in sheet.findall(f".//{_q('c')}"):
        if cell.get("r") == reference:
            return cell
    raise ValueError(f"Célula de referência ausente no modelo: {reference}")


def _set_text(sheet, reference, value):
    cell = _cell(sheet, reference)
    cell.set("t", "inlineStr")
    for child in list(cell): cell.remove(child)
    inline = ET.SubElement(cell, _q("is")); text = ET.SubElement(inline, _q("t")); text.text = str(value or "")


def _row_number(reference):
    return int(re.search(r"\d+$", reference).group())


def _copy_row_to(sheet_data, source_row, target_number):
    row = copy.deepcopy(source_row)
    row.set("r", str(target_number))
    row.set("ht", GRID_ROW_HEIGHT)
    row.set("customHeight", "1")
    for cell in row.findall(_q("c")):
        column = re.match(r"[A-Z]+", cell.get("r", "")).group()
        cell.set("r", f"{column}{target_number}")
    for current in list(sheet_data):
        if int(current.get("r", "0")) == target_number:
            sheet_data.remove(current)
            break
    preceding = next((node for node in reversed(list(sheet_data)) if int(node.get("r", "0")) < target_number), None)
    if preceding is None:
        sheet_data.insert(0, row)
    else:
        sheet_data.insert(list(sheet_data).index(preceding) + 1, row)
    return row


def _expand_grid(sheet, last_grid_row=GRID_LAST_ROW):
    sheet_data = sheet.find(_q("sheetData"))
    source_rows = {
        number: next(row for row in sheet_data if int(row.get("r", "0")) == number)
        for number in range(GRID_FIRST_ROW, GRID_FIRST_ROW + 4)
    }
    for number in range(GRID_FIRST_ROW, last_grid_row + 1):
        source_number = GRID_FIRST_ROW + (number - GRID_FIRST_ROW) % 4
        if number < GRID_FIRST_ROW + 4:
            row = next(row for row in sheet_data if int(row.get("r", "0")) == number)
            row.set("ht", GRID_ROW_HEIGHT)
            row.set("customHeight", "1")
        else:
            _copy_row_to(sheet_data, source_rows[source_number], number)


def _move_signatures(sheet, signature_row_1=SIGNATURE_ROW_1, signature_row_2=SIGNATURE_ROW_2):
    sheet_data = sheet.find(_q("sheetData"))
    for source_number, target_number in ((33, signature_row_1), (37, signature_row_2)):
        source = next(row for row in sheet_data if int(row.get("r", "0")) == source_number)
        _copy_row_to(sheet_data, source, target_number)
    merge = sheet.find(_q("mergeCells"))
    for node in list(merge) if merge is not None else []:
        if node.get("ref") == "D33:M33":
            node.set("ref", f"D{signature_row_1}:M{signature_row_1}")
        elif node.get("ref") == "D37:M37":
            node.set("ref", f"D{signature_row_2}:M{signature_row_2}")


def _compact_page_setup(sheet):
    margins = sheet.find(_q("pageMargins"))
    if margins is not None:
        for edge in ("left", "right"):
            margins.set(edge, "0.25")
        for edge in ("top", "bottom"):
            margins.set(edge, "0.30")
    setup = sheet.find(_q("pageSetup"))
    if setup is not None:
        setup.set("scale", "92")


def _replace_grid(sheet, rows, last_grid_row=GRID_LAST_ROW):
    merge = sheet.find(_q("mergeCells"))
    old = [] if merge is None else [
        node for node in list(merge)
        if not GRID_FIRST_ROW <= _row_number(node.get("ref", "A1").split(":")[0]) <= last_grid_row
    ]
    if merge is not None:
        merge.clear()
        for node in old: merge.append(node)
    else:
        merge = ET.SubElement(sheet, _q("mergeCells"))
    for row in range(GRID_FIRST_ROW, last_grid_row + 1):
        _set_text(sheet, f"A{row}", ""); _set_text(sheet, f"B{row}", ""); _set_text(sheet, f"C{row}", ""); _set_text(sheet, f"D{row}", "")
    for offset, item in enumerate(rows):
        _set_text(sheet, f"C{GRID_FIRST_ROW + offset}", item["casa"])
    start = 0
    while start < len(rows):
        end = start
        key = rows[start]["servico"].casefold()
        while end + 1 < len(rows) and (end + 1) % GRID_CAPACITY != 0 and rows[end + 1]["servico"].casefold() == key: end += 1
        first, last = GRID_FIRST_ROW + start, GRID_FIRST_ROW + end
        _set_text(sheet, f"A{first}", rows[start]["servico"])
        if last > first:
            merge.append(ET.Element(_q("mergeCell"), {"ref": f"A{first}:A{last}"}))
        block_start = start
        while block_start <= end:
            block_end = block_start; block = rows[block_start]["bloco"]
            while block_end + 1 <= end and (block_end + 1) % GRID_CAPACITY != 0 and rows[block_end + 1]["bloco"] == block: block_end += 1
            if block:
                row1, row2 = GRID_FIRST_ROW + block_start, GRID_FIRST_ROW + block_end
                _set_text(sheet, f"B{row1}", block)
                if row2 > row1:
                    merge.append(ET.Element(_q("mergeCell"), {"ref": f"B{row1}:B{row2}"}))
            block_start = block_end + 1
        start = end + 1
    for row in range(GRID_FIRST_ROW, last_grid_row + 1):
        merge.append(ET.Element(_q("mergeCell"), {"ref": f"D{row}:N{row}"}))
    merge.set("count", str(len(merge)))


def _trim_sheet(sheet, last_row):
    sheet_data = sheet.find(_q("sheetData"))
    for row in list(sheet_data):
        if int(row.get("r", "0")) > last_row:
            sheet_data.remove(row)
    dimension = sheet.find(_q("dimension"))
    if dimension is not None:
        dimension.set("ref", f"A1:N{last_row}")
    merge = sheet.find(_q("mergeCells"))
    if merge is not None:
        for node in list(merge):
            if _row_number(node.get("ref", "A1").split(":")[0]) > last_row:
                merge.remove(node)
        merge.set("count", str(len(merge)))


def _set_print_areas(workbook, page_name, print_last_row):
    defined = workbook.find(_q("definedNames"))
    if defined is None:
        defined = ET.Element(_q("definedNames"))
        sheets = workbook.find(_q("sheets"))
        workbook.insert(list(workbook).index(sheets) + 1, defined)
    for node in list(defined):
        if node.get("name") in ("_xlnm.Print_Area", "_xlnm.Print_Titles"):
            defined.remove(node)
    print_area = ET.SubElement(defined, _q("definedName"), {
        "name": "_xlnm.Print_Area", "localSheetId": "0", "hidden": "1"
    })
    print_area.text = f"'{page_name}'!$A$1:$N${print_last_row}"
    print_titles = ET.SubElement(defined, _q("definedName"), {
        "name": "_xlnm.Print_Titles", "localSheetId": "0", "hidden": "1"
    })
    print_titles.text = f"'{page_name}'!$1:$5"


def _set_page_breaks(sheet, page_count):
    for name in ("rowBreaks", "colBreaks"):
        existing = sheet.find(_q(name))
        if existing is not None:
            sheet.remove(existing)
    if page_count <= 1:
        return
    breaks = ET.Element(_q("rowBreaks"), {
        "count": str(page_count - 1), "manualBreakCount": str(page_count - 1)
    })
    for page_number in range(1, page_count):
        row = GRID_FIRST_ROW + page_number * GRID_CAPACITY - 1
        ET.SubElement(breaks, _q("brk"), {"id": str(row), "max": "16383", "man": "1"})
    setup = sheet.find(_q("pageSetup"))
    header_footer = sheet.find(_q("headerFooter"))
    anchor = header_footer if header_footer is not None else setup
    if anchor is None:
        sheet.append(breaks)
    else:
        sheet.insert(list(sheet).index(anchor) + 1, breaks)


def _prepare_package_relationships(files, page_count):
    relationships = ET.fromstring(files["xl/_rels/workbook.xml.rels"])
    worksheet_type = f"{REL}/worksheet"
    worksheet_paths = {}
    next_id = 1
    for relation in relationships:
        match = re.fullmatch(r"rId(\d+)", relation.get("Id", ""))
        if match:
            next_id = max(next_id, int(match.group(1)) + 1)
        if relation.get("Type") == worksheet_type:
            worksheet_paths[relation.get("Target", "")] = relation.get("Id")
    used = {relation.get("Id") for relation in relationships}
    relation_ids = []
    for page in range(1, page_count + 1):
        target = f"worksheets/sheet{page}.xml"
        relation_id = worksheet_paths.get(target)
        if relation_id is None:
            while f"rId{next_id}" in used:
                next_id += 1
            relation_id = f"rId{next_id}"
            next_id += 1
            used.add(relation_id)
            ET.SubElement(relationships, _pq("Relationship"), {
                "Id": relation_id, "Type": worksheet_type, "Target": target
            })
        relation_ids.append(relation_id)
    files["xl/_rels/workbook.xml.rels"] = ET.tostring(relationships, encoding="utf-8", xml_declaration=True)
    return relation_ids


def _prepare_sheet_relationships(files, page_count):
    base_relationships = files.get("xl/worksheets/_rels/sheet1.xml.rels")
    if base_relationships is None:
        return
    for page in range(2, page_count + 1):
        name = f"xl/worksheets/_rels/sheet{page}.xml.rels"
        if name not in files:
            files[name] = base_relationships


def _set_sheet_content_types(files, page_count):
    content_types = ET.fromstring(files["[Content_Types].xml"])
    worksheet_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    for node in list(content_types):
        if node.tag == _cq("Override") and re.fullmatch(r"/xl/worksheets/sheet\d+\.xml", node.get("PartName", "")):
            content_types.remove(node)
    for page in range(1, page_count + 1):
        ET.SubElement(content_types, _cq("Override"), {
            "PartName": f"/xl/worksheets/sheet{page}.xml", "ContentType": worksheet_type
        })
    files["[Content_Types].xml"] = ET.tostring(content_types, encoding="utf-8", xml_declaration=True)


def xlsx_bytes(proposal):
    if not TEMPLATE.exists(): raise RuntimeError("Modelo Excel da proposta de medição não foi encontrado")
    pages = _pages(_lines(proposal))
    rows = [row for page_rows in pages for row in page_rows]
    last_grid_row = GRID_FIRST_ROW + len(pages) * GRID_CAPACITY - 1
    signature_row_1, signature_row_2 = last_grid_row + 4, last_grid_row + 8
    signature_last_row = signature_row_2
    with zipfile.ZipFile(TEMPLATE, "r") as source:
        files = {name: source.read(name) for name in source.namelist()}
    base_sheet = ET.fromstring(files["xl/worksheets/sheet1.xml"])
    workbook = ET.fromstring(files["xl/workbook.xml"])
    page = copy.deepcopy(base_sheet)
    page_name = "Proposta"
    _set_text(page, "B1", "Proposta de medição")
    _set_text(page, "A3", f"EMPRESA: {proposal['empresa']}")
    _set_text(page, "B3", f"OBRA: {proposal['obra']}")
    date_text = proposal["data_proposta"].split("-")
    _set_text(page, "J3", f"DATA {date_text[2]}/{date_text[1]}/{date_text[0][2:]}")
    _move_signatures(page, signature_row_1, signature_row_2)
    _expand_grid(page, last_grid_row)
    _replace_grid(page, rows, last_grid_row)
    _compact_page_setup(page)
    _set_page_breaks(page, len(pages))
    _trim_sheet(page, signature_last_row)
    files["xl/worksheets/sheet1.xml"] = ET.tostring(page, encoding="utf-8", xml_declaration=True)
    sheets = workbook.find(_q("sheets")); sheets.clear()
    sheet = ET.SubElement(sheets, _q("sheet"), {"name": page_name, "sheetId": "1"})
    relation_ids = _prepare_package_relationships(files, 1)
    _prepare_sheet_relationships(files, 1)
    sheet.set(_rq("id"), relation_ids[0])
    _set_print_areas(workbook, page_name, signature_last_row)
    _set_sheet_content_types(files, 1)
    files["xl/workbook.xml"] = ET.tostring(workbook, encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in files.items(): target.writestr(name, data)
    return output.getvalue()


def _page_html(rows, proposal, page_number, total_pages, logo):
    rendered = []
    index = 0
    while index < len(rows):
        service_end = index
        while service_end + 1 < len(rows) and rows[service_end + 1]["servico"].casefold() == rows[index]["servico"].casefold(): service_end += 1
        service_span = service_end - index + 1
        block_index = index
        while block_index <= service_end:
            block_end = block_index
            while block_end + 1 <= service_end and rows[block_end + 1]["bloco"] == rows[block_index]["bloco"]: block_end += 1
            for item_index in range(block_index, block_end + 1):
                cells = []
                if item_index == index: cells.append(f'<td class="service" rowspan="{service_span}">{html.escape(rows[index]["servico"])}</td>')
                if item_index == block_index: cells.append(f'<td class="block" rowspan="{block_end-block_index+1}">{html.escape(rows[block_index]["bloco"])}</td>')
                cells.append(f'<td class="house">{html.escape(rows[item_index]["casa"])}</td><td class="notes"></td>')
                rendered.append("<tr>"+"".join(cells)+"</tr>")
            block_index = block_end + 1
        index = service_end + 1
    while len(rendered) < GRID_CAPACITY: rendered.append('<tr><td class="service"></td><td class="block"></td><td class="house"></td><td class="notes"></td></tr>')
    date = proposal["data_proposta"].split("-")
    image = f'<img src="data:image/png;base64,{logo}">' if logo else ''
    signatures = '''<footer><div>Ass.: Responsável pela Empresa</div><div>Ass.: Téc. de Qualidade</div><div>Ass.: Encarregado da Obra</div><div>Ass.: Estagiário de Engenharia</div></footer>''' if page_number == total_pages else ""
    return f'''<section class="page"><header>{image}<h1>Proposta de medição{f' · pág. {page_number}' if total_pages > 1 else ''}</h1></header>
      <div class="metadata"><b>EMPRESA: {html.escape(proposal['empresa'])}</b><b>OBRA: {html.escape(proposal['obra'])}</b><b>DATA {date[2]}/{date[1]}/{date[0][2:]}</b></div>
      <table><thead><tr><th>SERVIÇOS</th><th>BLOCO</th><th>CASA</th><th>OBSERVAÇÕES</th></tr></thead><tbody>{''.join(rendered)}</tbody></table>
      {signatures}</section>'''


def pdf_bytes(proposal):
    """Renderiza as mesmas páginas do modelo em PDF, sem converter o XLSX para imagem."""
    with tempfile.TemporaryDirectory(prefix="proposta-medicao-") as directory:
        directory = Path(directory)
        logo = ""
        with zipfile.ZipFile(TEMPLATE, "r") as template:
            try: logo = base64.b64encode(template.read("xl/media/image1.png")).decode("ascii")
            except KeyError: pass
        pages = _pages(_lines(proposal))
        document = """<!doctype html><html><head><meta charset='utf-8'><style>
          @page{size:A4 portrait;margin:0}*{box-sizing:border-box}body{margin:0;font-family:Calibri,Arial,sans-serif;color:#000}.page{height:297mm;padding:6mm;page-break-after:always;position:relative}.page:last-child{page-break-after:auto}header{height:17mm;display:flex;align-items:center;justify-content:center;position:relative}header img{position:absolute;left:0;top:1mm;width:46mm;max-height:10mm;object-fit:contain}h1{font-size:15pt;margin:0}.metadata{height:7mm;border:1pt solid #000;display:grid;grid-template-columns:38% 42% 20%;align-items:center;font-size:9pt}.metadata b{padding:.7mm;border-right:.5pt solid #000;height:100%}.metadata b:last-child{border-right:0;text-align:center}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:.45pt solid #000}th{height:8mm;background:#d6dce4;font-size:9pt;text-align:center}th:nth-child(1){width:37%}th:nth-child(2){width:9%}th:nth-child(3){width:9%}th:nth-child(4){width:45%}td{height:4.5mm;font-size:8pt;text-align:center;vertical-align:middle;padding:.25mm .6mm}.service{font-weight:700;word-break:break-word}.block,.house{font-weight:600}.notes{text-align:left}footer{position:absolute;bottom:6mm;display:grid;grid-template-columns:1fr 1fr;gap:8mm 30mm;width:calc(100% - 12mm);font-size:8pt}footer div{border-top:.5pt solid #000;padding-top:.6mm;text-align:center}
          </style></head><body>""" + "".join(_page_html(rows, proposal, number, len(pages), logo) for number, rows in enumerate(pages,1)) + "</body></html>"
        source = directory / "proposta.html"; pdf = directory / "proposta.pdf"; profile = directory / "chrome-profile"; source.write_text(document,encoding="utf-8")
        result = subprocess.run(["google-chrome","--headless=new","--no-sandbox",f"--user-data-dir={profile}",f"--print-to-pdf={pdf}",source.as_uri()], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
        if result.returncode != 0 or not pdf.exists():
            raise RuntimeError("Não foi possível converter a proposta para PDF")
        return pdf.read_bytes()
