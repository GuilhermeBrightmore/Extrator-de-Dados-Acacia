#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extrator de Perfis da Plataforma Acácia — versão 3
---------------------------------------------------
Aplicativo Tkinter para:
1. Consultar um perfil individual por slug, nome ou URL.
2. Importar um CSV no formato nome;urlacacia (ou nome;url_acacia).
3. Processar vários perfis em sequência, sem bloquear a interface.
4. Exibir identidade acadêmica, métricas topológicas e percentis.
5. Exportar resultados individuais ou uma base consolidada em CSV/JSON.

Dependências externas:
    pip install beautifulsoup4 requests
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Tkinter não está disponível nesta instalação do Python.") from exc

try:
    from bs4 import BeautifulSoup
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Dependência ausente",
        "A biblioteca BeautifulSoup não está instalada.\n\n"
        "Execute no Prompt de Comando ou PowerShell:\n"
        "pip install beautifulsoup4",
    )
    root.destroy()
    raise SystemExit(1)

try:
    import requests
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Dependência ausente",
        "A biblioteca Requests não está instalada.\n\n"
        "Execute no Prompt de Comando ou PowerShell:\n"
        "pip install requests",
    )
    root.destroy()
    raise SystemExit(1)


APP_TITLE = "Extrator de Perfis Acácia"
APP_VERSION = "3.0"
BASE_PROFILE_URL = "https://plataforma-acacia.org/profile/"
HTTP_TIMEOUT = (10, 40)
REQUEST_INTERVAL_SECONDS = 0.35

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}

METRIC_ORDER = ["IG", "DS", "FC", "FT", "G", "R", "PR"]
METRIC_LABELS = {
    "IG": "Índice Genealógico",
    "DS": "Descendência",
    "FC": "Fecundidade",
    "FT": "Fertilidade",
    "G": "Gerações",
    "R": "Relações",
    "PR": "Primos",
}
JSON_TO_CODE = {
    "gi": "IG",
    "dp": "DS",
    "fcp": "FC",
    "ftp": "FT",
    "gp": "G",
    "rp": "R",
    "cp": "PR",
}
POSITION_FIELDS = {
    "global": "entre_doutores_orientadores",
    "my": "mesmo_ano_primeira_orientacao",
    "mak": "mesma_grande_area",
    "ak": "mesma_area",
}


@dataclass
class AcademicIdentity:
    nome: str = ""
    grande_area: str = ""
    area: str = ""
    instituicao: str = ""
    primeira_orientacao: str = ""
    lattes_id: str = ""
    lattes_url: str = ""
    atualizacao_lattes: str = ""
    perfil_url: str = ""


@dataclass
class Positioning:
    codigo: str
    metrica: str
    valor: float | int | str
    entre_doutores_orientadores: Optional[float] = None
    mesmo_ano_primeira_orientacao: Optional[float] = None
    mesma_grande_area: Optional[float] = None
    mesma_area: Optional[float] = None


@dataclass
class ProfileData:
    identidade: AcademicIdentity = field(default_factory=AcademicIdentity)
    metricas: dict[str, float | int | str] = field(default_factory=dict)
    posicionamentos: list[Positioning] = field(default_factory=list)
    rotulos_comparacao: dict[str, str] = field(default_factory=dict)
    arquivo_origem: str = ""
    avisos: list[str] = field(default_factory=list)


@dataclass
class BatchInput:
    ordem: int
    nome_informado: str
    url_informada: str
    slug: str
    url_consulta: str


@dataclass
class BatchResult:
    entrada: BatchInput
    status: str = "Pendente"
    erro: str = ""
    perfil: Optional[ProfileData] = None


class AcaciaParser:
    """Parser tolerante a pequenas mudanças estruturais no HTML do perfil."""

    def parse_file(self, file_path: str | Path) -> ProfileData:
        path = Path(file_path)
        html = self._read_html(path)
        data = self.parse_html(html)
        data.arquivo_origem = str(path)
        return data

    @staticmethod
    def _read_html(path: Path) -> str:
        raw = path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def parse_html(self, html: str | bytes) -> ProfileData:
        soup = BeautifulSoup(html, "html.parser")
        data = ProfileData()
        data.identidade = self._extract_identity(soup)
        data.metricas = self._extract_metrics(soup)
        metrics_json = self._extract_metrics_json(soup, data.avisos)
        data.rotulos_comparacao = self._extract_scope_labels(soup, data.identidade)
        data.posicionamentos = self._build_positioning(data.metricas, metrics_json)

        if not data.identidade.nome:
            data.avisos.append("Nome do pesquisador não localizado.")
        if not data.metricas:
            data.avisos.append("Tabela de métricas topológicas não localizada.")
        if not metrics_json:
            data.avisos.append("Dados de posicionamento percentílico não localizados.")
        return data

    def _extract_identity(self, soup: BeautifulSoup) -> AcademicIdentity:
        identity = AcademicIdentity()

        name_node = soup.select_one('h1[itemprop="name"]') or soup.select_one("h1.title")
        if name_node:
            identity.nome = self._clean(name_node.get_text(" ", strip=True))

        profile_meta = soup.select_one('meta[itemprop="url"]')
        if profile_meta:
            identity.perfil_url = self._clean(profile_meta.get("content", ""))

        classification = soup.select(".profile-identity-classification .profile-identity-meta-item")
        values = [self._clean(node.get_text(" ", strip=True)) for node in classification]
        values = [value for value in values if value]
        if values:
            identity.grande_area = values[0]
        if len(values) > 1:
            identity.area = values[1]

        institution_node = soup.select_one(
            '.profile-identity-item [itemtype="https://schema.org/CollegeOrUniversity"] [itemprop="name"], '
            '.profile-identity-item [itemtype="http://schema.org/CollegeOrUniversity"] [itemprop="name"]'
        )
        if institution_node:
            identity.instituicao = self._clean(institution_node.get_text(" ", strip=True))

        all_identity_text = "\n".join(
            self._clean(node.get_text(" ", strip=True))
            for node in soup.select(".profile-identity-item")
        )

        first_orientation = re.search(
            r"Primeira\s+orienta(?:ç|c)[aã]o\s+conclu[ií]da\s+em\s+(\d{4})",
            all_identity_text,
            flags=re.IGNORECASE,
        )
        if first_orientation:
            identity.primeira_orientacao = first_orientation.group(1)

        lattes_link = None
        for link in soup.select("a[href]"):
            href = link.get("href", "")
            if "lattes.cnpq.br" in href.lower():
                lattes_link = link
                break
        if lattes_link:
            identity.lattes_url = self._clean(lattes_link.get("href", ""))
            match = re.search(r"(\d{16})", identity.lattes_url)
            if match:
                identity.lattes_id = match.group(1)

        updated = re.search(
            r"Curr[ií]culo\s+Lattes\s+atualizado\s+em\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})",
            all_identity_text,
            flags=re.IGNORECASE,
        )
        if updated:
            identity.atualizacao_lattes = updated.group(1)

        return identity

    def _extract_metrics(self, soup: BeautifulSoup) -> dict[str, float | int | str]:
        metrics: dict[str, float | int | str] = {}
        table = soup.select_one(".profile-metric-table")
        if not table:
            heading = soup.find(
                lambda tag: tag.name in {"h2", "h3"}
                and "Métricas topológicas" in tag.get_text(" ", strip=True)
            )
            if heading:
                container = heading.find_parent(["article", "div"])
                table = container.find("table") if container else None

        if not table:
            return metrics

        for row in table.select("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = self._clean(cells[0].get_text(" ", strip=True))
            value_text = self._clean(cells[-1].get_text(" ", strip=True))
            code_match = re.search(r"\((IG|DS|FC|FT|G|R|PR)\)", label, flags=re.IGNORECASE)
            if code_match:
                code = code_match.group(1).upper()
            else:
                code_node = cells[0].select_one(".profile-metric-code")
                code = self._clean(code_node.get_text(strip=True)).upper() if code_node else ""
            if code in METRIC_LABELS:
                metrics[code] = self._parse_number(value_text)
        return metrics

    def _extract_metrics_json(self, soup: BeautifulSoup, warnings: list[str]) -> dict[str, Any]:
        script = soup.select_one("script#metrics-data")
        if not script:
            return {}
        payload = script.string or script.get_text(strip=True)
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError as exc:
            warnings.append(f"Falha ao interpretar o JSON de posicionamento: {exc}")
            return {}

    def _extract_scope_labels(self, soup: BeautifulSoup, identity: AcademicIdentity) -> dict[str, str]:
        labels = {
            "global": "Entre doutores orientadores",
            "my": "No mesmo ano da 1ª orientação",
            "mak": f"Na Grande Área {identity.grande_area}".strip(),
            "ak": f"Na Área {identity.area}".strip(),
        }
        id_map = {
            "metric-global": "global",
            "metric-my": "my",
            "metric-mak": "mak",
            "metric-ak": "ak",
        }
        for element_id, scope in id_map.items():
            row = soup.select_one(f"#{element_id}")
            if row:
                label_node = row.select_one(".profile-comparison-label")
                if label_node:
                    labels[scope] = self._clean(label_node.get_text(" ", strip=True))
        return labels

    def _build_positioning(
        self,
        metrics: dict[str, float | int | str],
        metrics_json: dict[str, Any],
    ) -> list[Positioning]:
        output: list[Positioning] = []
        for code in METRIC_ORDER:
            json_key = next((key for key, value in JSON_TO_CODE.items() if value == code), None)
            scopes = metrics_json.get(json_key, {}) if json_key else {}
            if not isinstance(scopes, dict):
                scopes = {}
            output.append(
                Positioning(
                    codigo=code,
                    metrica=METRIC_LABELS[code],
                    valor=metrics.get(code, ""),
                    entre_doutores_orientadores=self._optional_float(scopes.get("global")),
                    mesmo_ano_primeira_orientacao=self._optional_float(scopes.get("my")),
                    mesma_grande_area=self._optional_float(scopes.get("mak")),
                    mesma_area=self._optional_float(scopes.get("ak")),
                )
            )
        return output

    @staticmethod
    def _clean(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _parse_number(text: str) -> float | int | str:
        normalized = text.strip().replace(".", "").replace(",", ".")
        if re.fullmatch(r"[-+]?\d+", normalized):
            return int(normalized)
        if re.fullmatch(r"[-+]?\d*\.\d+", normalized):
            return float(normalized)
        return text.strip()

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class BatchCSVReader:
    """Lê listas de pesquisadores com cabeçalhos flexíveis."""

    NAME_ALIASES = {"nome", "name", "pesquisador", "docente", "researcher"}
    URL_ALIASES = {
        "urlacacia",
        "url_acacia",
        "url",
        "link",
        "perfil",
        "perfilacacia",
        "perfil_acacia",
        "acacia",
    }

    @classmethod
    def read(cls, file_path: str | Path) -> list[BatchInput]:
        path = Path(file_path)
        text = cls._decode(path.read_bytes())
        delimiter = cls._detect_delimiter(text)
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("O arquivo CSV não possui cabeçalho.")

        normalized_fields = {
            cls._normalize_header(field): field
            for field in reader.fieldnames
            if field is not None
        }
        name_field = cls._find_field(normalized_fields, cls.NAME_ALIASES)
        url_field = cls._find_field(normalized_fields, cls.URL_ALIASES)
        if not name_field or not url_field:
            raise ValueError(
                "O CSV deve possuir as colunas 'nome' e 'urlacacia' ou 'url_acacia'.\n\n"
                f"Cabeçalhos encontrados: {', '.join(reader.fieldnames)}"
            )

        rows: list[BatchInput] = []
        for line_number, row in enumerate(reader, start=2):
            name = str(row.get(name_field, "") or "").strip()
            url_value = str(row.get(url_field, "") or "").strip()
            if not name and not url_value:
                continue
            slug = normalize_slug(url_value or name)
            if not slug:
                raise ValueError(f"Linha {line_number}: não foi possível identificar o slug.")
            url = f"{BASE_PROFILE_URL}{slug}/"
            rows.append(
                BatchInput(
                    ordem=len(rows) + 1,
                    nome_informado=name,
                    url_informada=url_value,
                    slug=slug,
                    url_consulta=url,
                )
            )

        if not rows:
            raise ValueError("O CSV não contém registros válidos para processamento.")
        return rows

    @staticmethod
    def _decode(raw: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _detect_delimiter(text: str) -> str:
        sample = "\n".join(text.splitlines()[:20])
        try:
            return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
        except csv.Error:
            return ";"

    @staticmethod
    def _normalize_header(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(char for char in text if not unicodedata.combining(char))
        return re.sub(r"[^a-z0-9_]+", "", text.lower().strip())

    @staticmethod
    def _find_field(mapping: dict[str, str], aliases: set[str]) -> Optional[str]:
        for alias in aliases:
            normalized = BatchCSVReader._normalize_header(alias)
            if normalized in mapping:
                return mapping[normalized]
        return None


def normalize_slug(value: str) -> str:
    """Aceita slug, nome completo ou URL e devolve um slug ASCII normalizado."""
    text = str(value or "").strip()
    if not text:
        return ""

    if "://" in text:
        parsed = urlparse(text)
        parts = [part for part in parsed.path.split("/") if part]
        if "profile" in parts:
            index = parts.index("profile")
            text = parts[index + 1] if len(parts) > index + 1 else ""
        elif parts:
            text = parts[-1]
        else:
            text = ""
    else:
        text = re.sub(r"^/?profile/", "", text, flags=re.IGNORECASE).strip("/")

    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


class AcaciaExtractorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} — v{APP_VERSION}")
        self.geometry("1280x820")
        self.minsize(1040, 690)
        self.parser = AcaciaParser()
        self.profile_data: Optional[ProfileData] = None
        self.batch_inputs: list[BatchInput] = []
        self.batch_results: list[BatchResult] = []
        self.batch_source_file = ""
        self.batch_stop_event = threading.Event()
        self.slug_var = tk.StringVar()
        self.current_file = tk.StringVar(value="Nenhuma fonte carregada")
        self.status = tk.StringVar(value="Digite um slug ou importe uma lista CSV.")
        self.batch_status = tk.StringVar(value="Nenhum lote carregado.")
        self.round_percentiles = tk.BooleanVar(value=False)
        self.is_loading = False
        self.is_batch_loading = False

        self._configure_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self.slug_entry.focus_set)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Treeview", rowheight=27, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Consulte um pesquisador ou processe uma lista CSV com vários perfis.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        input_area = ttk.Frame(self, padding=(16, 2, 16, 6))
        input_area.pack(fill="x")
        input_area.columnconfigure(0, weight=1)
        input_area.columnconfigure(1, weight=1)

        search_frame = ttk.LabelFrame(input_area, text="Consulta individual", padding=(12, 10))
        search_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ttk.Label(search_frame, text="Slug, nome ou URL do perfil:").grid(row=0, column=0, sticky="w")
        self.slug_entry = ttk.Entry(search_frame, textvariable=self.slug_var, font=("Segoe UI", 11))
        self.slug_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.slug_entry.bind("<Return>", lambda _event: self.fetch_profile())
        self.fetch_button = ttk.Button(
            search_frame,
            text="Buscar perfil",
            style="Accent.TButton",
            command=self.fetch_profile,
        )
        self.fetch_button.grid(row=1, column=1, padx=(8, 0), pady=(4, 0), sticky="ew")
        ttk.Label(search_frame, text="Exemplo: adriana-lemos-pereira", foreground="#555555").grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        search_frame.columnconfigure(0, weight=1)

        batch_frame = ttk.LabelFrame(input_area, text="Processamento em lote", padding=(12, 10))
        batch_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        batch_buttons = ttk.Frame(batch_frame)
        batch_buttons.grid(row=0, column=0, sticky="ew")
        self.import_batch_button = ttk.Button(
            batch_buttons,
            text="Importar CSV e processar",
            style="Accent.TButton",
            command=self.import_and_process_csv,
        )
        self.import_batch_button.pack(side="left")
        self.stop_batch_button = ttk.Button(
            batch_buttons,
            text="Interromper",
            state="disabled",
            command=self.stop_batch,
        )
        self.stop_batch_button.pack(side="left", padx=(8, 0))
        ttk.Button(batch_buttons, text="Modelo CSV", command=self.save_csv_template).pack(side="left", padx=(8, 0))

        self.batch_progress = ttk.Progressbar(batch_frame, mode="determinate", maximum=1)
        self.batch_progress.grid(row=1, column=0, sticky="ew", pady=(8, 3))
        ttk.Label(batch_frame, textvariable=self.batch_status, foreground="#444444").grid(
            row=2, column=0, sticky="w"
        )
        batch_frame.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(self, padding=(16, 5, 16, 9))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Abrir HTML local", command=self.open_html).pack(side="left")
        ttk.Button(toolbar, text="Exportar perfil CSV", command=self.export_csv).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Exportar perfil JSON", command=self.export_json).pack(side="left", padx=(8, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        self.export_batch_csv_button = ttk.Button(
            toolbar, text="Exportar lote CSV", command=self.export_batch_csv, state="disabled"
        )
        self.export_batch_csv_button.pack(side="left")
        self.export_batch_json_button = ttk.Button(
            toolbar, text="Exportar lote JSON", command=self.export_batch_json, state="disabled"
        )
        self.export_batch_json_button.pack(side="left", padx=(8, 0))
        self.clear_batch_button = ttk.Button(
            toolbar, text="Limpar lote", command=self.clear_batch, state="disabled"
        )
        self.clear_batch_button.pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Limpar perfil", command=self.clear_profile).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            toolbar,
            text="Arredondar percentis na tela",
            variable=self.round_percentiles,
            command=self.refresh_positioning,
        ).pack(side="right")

        file_frame = ttk.Frame(self, padding=(16, 0, 16, 8))
        file_frame.pack(fill="x")
        ttk.Label(file_frame, text="Fonte ativa:", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(file_frame, textvariable=self.current_file).pack(side="left", padx=(5, 0), fill="x", expand=True)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        self.tab_batch = ttk.Frame(self.notebook, padding=10)
        self.tab_identity = ttk.Frame(self.notebook, padding=10)
        self.tab_metrics = ttk.Frame(self.notebook, padding=10)
        self.tab_positioning = ttk.Frame(self.notebook, padding=10)
        self.tab_summary = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.tab_batch, text="Resultados do lote")
        self.notebook.add(self.tab_identity, text="Identidade acadêmica")
        self.notebook.add(self.tab_metrics, text="Métricas topológicas")
        self.notebook.add(self.tab_positioning, text="Posicionamento")
        self.notebook.add(self.tab_summary, text="Resumo textual")

        batch_top = ttk.Frame(self.tab_batch)
        batch_top.pack(fill="x", pady=(0, 7))
        ttk.Label(
            batch_top,
            text="Dê duplo clique em um pesquisador concluído para abrir seu resultado nas demais abas.",
        ).pack(side="left")
        ttk.Button(batch_top, text="Abrir selecionado", command=self.open_selected_batch_result).pack(side="right")

        self.batch_tree = self._make_tree(
            self.tab_batch,
            columns=("n", "nome_csv", "nome_perfil", "status", "ig", "ds", "fc", "ft", "g", "r", "pr", "avisos"),
            headings=("N", "Nome informado", "Nome no perfil", "Status", "IG", "DS", "FC", "FT", "G", "R", "PR", "Avisos/erro"),
            widths=(45, 235, 235, 105, 55, 55, 55, 55, 55, 55, 70, 300),
        )
        self.batch_tree.bind("<Double-1>", lambda _event: self.open_selected_batch_result())

        self.identity_tree = self._make_tree(
            self.tab_identity,
            columns=("campo", "valor"),
            headings=("Campo", "Valor"),
            widths=(280, 800),
        )
        self.metrics_tree = self._make_tree(
            self.tab_metrics,
            columns=("codigo", "metrica", "valor"),
            headings=("Código", "Métrica", "Valor"),
            widths=(120, 520, 220),
        )
        self.position_tree = self._make_tree(
            self.tab_positioning,
            columns=("codigo", "metrica", "valor", "global", "my", "mak", "ak"),
            headings=(
                "Código",
                "Métrica",
                "Valor",
                "Doutores orientadores",
                "Mesmo ano",
                "Grande área",
                "Área",
            ),
            widths=(80, 210, 100, 170, 150, 150, 150),
        )

        self.summary_text = tk.Text(
            self.tab_summary,
            wrap="word",
            font=("Segoe UI", 11),
            padx=14,
            pady=14,
            undo=False,
        )
        summary_scroll = ttk.Scrollbar(self.tab_summary, orient="vertical", command=self.summary_text.yview)
        self.summary_text.configure(yscrollcommand=summary_scroll.set)
        self.summary_text.pack(side="left", fill="both", expand=True)
        summary_scroll.pack(side="right", fill="y")
        self.summary_text.configure(state="disabled")

        status_bar = ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=(8, 5))
        status_bar.pack(fill="x", side="bottom")

    @staticmethod
    def _make_tree(
        parent: ttk.Frame,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        widths: tuple[int, ...],
    ) -> ttk.Treeview:
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        tree = ttk.Treeview(container, columns=columns, show="headings")
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            anchor = "center" if column in {"n", "ig", "ds", "fc", "ft", "g", "r", "pr"} else "w"
            tree.column(column, width=width, minwidth=45, anchor=anchor)
        vertical = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        return tree

    # ------------------------------------------------------------------
    # Consulta individual
    # ------------------------------------------------------------------
    def fetch_profile(self) -> None:
        if self.is_loading or self.is_batch_loading:
            return
        raw_value = self.slug_var.get().strip()
        slug = normalize_slug(raw_value)
        if not slug:
            messagebox.showwarning(
                "Slug necessário",
                "Informe o slug, o nome completo ou a URL do perfil.\n\n"
                "Exemplo: adriana-lemos-pereira",
            )
            self.slug_entry.focus_set()
            return
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            messagebox.showerror("Slug inválido", "Não foi possível converter o valor em um slug válido.")
            return

        self.slug_var.set(slug)
        url = f"{BASE_PROFILE_URL}{slug}/"
        self._set_loading(True, f"Baixando e analisando {url}")
        threading.Thread(target=self._fetch_profile_worker, args=(url,), daemon=True).start()

    def _fetch_profile_worker(self, url: str) -> None:
        try:
            with requests.Session() as session:
                session.headers.update(REQUEST_HEADERS)
                data, final_url = self._download_and_parse(session, url)
            self.after(0, lambda: self._finish_remote_load(data, final_url))
        except Exception as exc:
            detail = self._friendly_error(exc)
            self.after(0, lambda detail=detail: self._show_fetch_error(detail))

    def _download_and_parse(self, session: requests.Session, url: str) -> tuple[ProfileData, str]:
        response = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and "html" not in content_type:
            raise ValueError(f"O servidor retornou '{content_type}', e não uma página HTML.")
        data = self.parser.parse_html(response.content)
        data.arquivo_origem = response.url
        data.identidade.perfil_url = response.url
        if not data.identidade.nome:
            raise ValueError(
                "A página foi recebida, mas não parece conter um perfil válido da Plataforma Acácia."
            )
        return data, response.url

    def _finish_remote_load(self, data: ProfileData, source_url: str) -> None:
        self.profile_data = data
        self.current_file.set(source_url)
        self.populate_views()
        warning_suffix = f" Avisos: {len(data.avisos)}." if data.avisos else ""
        self._set_loading(False)
        self.status.set(f"Perfil de {data.identidade.nome} carregado.{warning_suffix}")
        self.notebook.select(self.tab_identity)

    def _show_fetch_error(self, detail: str) -> None:
        self._set_loading(False)
        self.status.set("Falha ao baixar ou processar o perfil.")
        messagebox.showerror("Erro na consulta", f"Não foi possível obter o perfil.\n\n{detail}")
        self.slug_entry.focus_set()

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        if isinstance(exc, requests.exceptions.HTTPError):
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                return "Perfil não encontrado (HTTP 404). Verifique o slug ou a URL."
            if status_code:
                return f"A Plataforma Acácia respondeu com o código HTTP {status_code}."
        if isinstance(exc, requests.exceptions.SSLError):
            return "Não foi possível validar o certificado de segurança da conexão."
        if isinstance(exc, requests.exceptions.Timeout):
            return "A Plataforma Acácia demorou demais para responder."
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "Não foi possível conectar à Plataforma Acácia. Verifique a internet."
        return str(exc) or exc.__class__.__name__

    def _set_loading(self, loading: bool, status_text: str = "") -> None:
        self.is_loading = loading
        self.fetch_button.configure(state="disabled" if loading or self.is_batch_loading else "normal")
        self.slug_entry.configure(state="disabled" if loading or self.is_batch_loading else "normal")
        if status_text:
            self.status.set(status_text)
        self._refresh_cursor()

    # ------------------------------------------------------------------
    # Processamento em lote
    # ------------------------------------------------------------------
    def import_and_process_csv(self) -> None:
        if self.is_loading or self.is_batch_loading:
            return
        file_path = filedialog.askopenfilename(
            title="Selecione a lista CSV de perfis Acácia",
            filetypes=[("Arquivos CSV", "*.csv"), ("Todos os arquivos", "*.*")],
        )
        if not file_path:
            return
        try:
            inputs = BatchCSVReader.read(file_path)
        except Exception as exc:
            messagebox.showerror("Erro no CSV", f"Não foi possível ler a lista.\n\n{exc}")
            return

        if self.batch_results:
            replace = messagebox.askyesno(
                "Substituir lote",
                "Já existem resultados de um lote anterior. Deseja substituí-los?",
            )
            if not replace:
                return

        self.batch_source_file = file_path
        self.batch_inputs = inputs
        self.batch_results = [BatchResult(entrada=item) for item in inputs]
        self.batch_stop_event.clear()
        self._populate_batch_pending()
        self.batch_progress.configure(maximum=len(inputs), value=0)
        self.batch_status.set(f"Lista carregada: {len(inputs)} pesquisadores. Iniciando consultas...")
        self.current_file.set(file_path)
        self.notebook.select(self.tab_batch)
        self._set_batch_loading(True)
        threading.Thread(target=self._batch_worker, daemon=True).start()

    def _batch_worker(self) -> None:
        with requests.Session() as session:
            session.headers.update(REQUEST_HEADERS)
            total = len(self.batch_results)
            for index, result in enumerate(self.batch_results):
                if self.batch_stop_event.is_set():
                    for remaining in self.batch_results[index:]:
                        remaining.status = "Interrompido"
                    self.after(0, lambda start=index: self._mark_remaining_interrupted(start))
                    break

                result.status = "Consultando"
                self.after(0, lambda idx=index: self._update_batch_row(idx))
                try:
                    data, final_url = self._download_and_parse(session, result.entrada.url_consulta)
                    data.arquivo_origem = final_url
                    data.identidade.perfil_url = final_url
                    result.perfil = data
                    result.status = "Concluído"
                except Exception as exc:
                    result.status = "Erro"
                    result.erro = self._friendly_error(exc)

                completed = index + 1
                self.after(0, lambda idx=index, done=completed, total=total: self._batch_item_finished(idx, done, total))
                if completed < total and not self.batch_stop_event.is_set():
                    time.sleep(REQUEST_INTERVAL_SECONDS)

        self.after(0, self._finish_batch)

    def _populate_batch_pending(self) -> None:
        self._clear_tree(self.batch_tree)
        for index, result in enumerate(self.batch_results):
            self.batch_tree.insert("", "end", iid=str(index), values=self._batch_tree_values(result))

    def _update_batch_row(self, index: int) -> None:
        if self.batch_tree.exists(str(index)):
            self.batch_tree.item(str(index), values=self._batch_tree_values(self.batch_results[index]))
        current = index + 1
        total = len(self.batch_results)
        self.batch_status.set(f"Consultando {current} de {total}: {self.batch_results[index].entrada.nome_informado}")

    def _batch_item_finished(self, index: int, completed: int, total: int) -> None:
        self._update_batch_row(index)
        self.batch_progress.configure(value=completed)
        successful = sum(1 for item in self.batch_results[:completed] if item.status == "Concluído")
        errors = sum(1 for item in self.batch_results[:completed] if item.status == "Erro")
        self.batch_status.set(
            f"Processados {completed}/{total} — concluídos: {successful}; erros: {errors}."
        )

    def _mark_remaining_interrupted(self, start_index: int) -> None:
        for index in range(start_index, len(self.batch_results)):
            self._update_batch_row(index)

    def _finish_batch(self) -> None:
        self._set_batch_loading(False)
        total = len(self.batch_results)
        successful = sum(1 for item in self.batch_results if item.status == "Concluído")
        errors = sum(1 for item in self.batch_results if item.status == "Erro")
        interrupted = sum(1 for item in self.batch_results if item.status == "Interrompido")
        self.batch_progress.configure(value=sum(1 for item in self.batch_results if item.status != "Pendente"))
        self.batch_status.set(
            f"Lote finalizado — total: {total}; concluídos: {successful}; erros: {errors}; interrompidos: {interrupted}."
        )
        self.status.set("Processamento em lote finalizado.")
        if successful:
            first_index = next(index for index, item in enumerate(self.batch_results) if item.status == "Concluído")
            self.batch_tree.selection_set(str(first_index))
            self.batch_tree.focus(str(first_index))

    def stop_batch(self) -> None:
        if not self.is_batch_loading:
            return
        self.batch_stop_event.set()
        self.stop_batch_button.configure(state="disabled")
        self.batch_status.set("Interrupção solicitada. A consulta atual será concluída antes de parar.")

    def _set_batch_loading(self, loading: bool) -> None:
        self.is_batch_loading = loading
        self.import_batch_button.configure(state="disabled" if loading or self.is_loading else "normal")
        self.stop_batch_button.configure(state="normal" if loading else "disabled")
        self.fetch_button.configure(state="disabled" if loading or self.is_loading else "normal")
        self.slug_entry.configure(state="disabled" if loading or self.is_loading else "normal")
        has_results = bool(self.batch_results) and not loading
        self.export_batch_csv_button.configure(state="normal" if has_results else "disabled")
        self.export_batch_json_button.configure(state="normal" if has_results else "disabled")
        self.clear_batch_button.configure(state="normal" if has_results else "disabled")
        self._refresh_cursor()

    def _refresh_cursor(self) -> None:
        self.configure(cursor="watch" if self.is_loading or self.is_batch_loading else "")
        self.update_idletasks()

    def _batch_tree_values(self, result: BatchResult) -> tuple[Any, ...]:
        profile = result.perfil
        metrics = profile.metricas if profile else {}
        profile_name = profile.identidade.nome if profile else ""
        details = result.erro
        if profile and profile.avisos:
            details = " | ".join(profile.avisos)
        return (
            result.entrada.ordem,
            result.entrada.nome_informado,
            profile_name,
            result.status,
            metrics.get("IG", ""),
            metrics.get("DS", ""),
            metrics.get("FC", ""),
            metrics.get("FT", ""),
            metrics.get("G", ""),
            metrics.get("R", ""),
            metrics.get("PR", ""),
            details,
        )

    def open_selected_batch_result(self) -> None:
        selected = self.batch_tree.selection()
        if not selected:
            messagebox.showwarning("Nenhuma seleção", "Selecione um pesquisador na tabela do lote.")
            return
        try:
            index = int(selected[0])
            result = self.batch_results[index]
        except (ValueError, IndexError):
            return
        if not result.perfil:
            messagebox.showwarning(
                "Resultado indisponível",
                f"Este registro está com status '{result.status}' e não possui perfil carregado.",
            )
            return
        self.profile_data = result.perfil
        self.current_file.set(result.perfil.arquivo_origem)
        self.slug_var.set(result.entrada.slug)
        self.populate_views()
        self.status.set(f"Resultado do lote aberto: {result.perfil.identidade.nome}.")
        self.notebook.select(self.tab_identity)

    def save_csv_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Salvar modelo de CSV",
            defaultextension=".csv",
            initialfile="modelo_perfis_acacia.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["nome", "urlacacia"])
            writer.writerow([
                "Adriana Lemos Pereira",
                "https://plataforma-acacia.org/profile/adriana-lemos-pereira/",
            ])
        self.status.set(f"Modelo CSV salvo: {path}")

    # ------------------------------------------------------------------
    # Visualização de um perfil
    # ------------------------------------------------------------------
    def open_html(self) -> None:
        if self.is_loading or self.is_batch_loading:
            return
        file_path = filedialog.askopenfilename(
            title="Selecione o HTML do perfil Acácia",
            filetypes=[("Arquivos HTML", "*.html *.htm"), ("Todos os arquivos", "*.*")],
        )
        if not file_path:
            return
        try:
            data = self.parser.parse_file(file_path)
        except Exception as exc:
            messagebox.showerror("Erro de leitura", f"Não foi possível processar o arquivo:\n\n{exc}")
            self.status.set("Falha ao processar o HTML.")
            return
        self.profile_data = data
        self.current_file.set(file_path)
        self.populate_views()
        warning_suffix = f" Avisos: {len(data.avisos)}." if data.avisos else ""
        self.status.set(f"Perfil de {data.identidade.nome or 'pesquisador desconhecido'} carregado.{warning_suffix}")
        self.notebook.select(self.tab_identity)

    def populate_views(self) -> None:
        if not self.profile_data:
            return
        data = self.profile_data
        identity = data.identidade

        self._clear_tree(self.identity_tree)
        identity_rows = [
            ("Nome", identity.nome),
            ("Grande Área", identity.grande_area),
            ("Área", identity.area),
            ("Instituição", identity.instituicao),
            ("Primeira orientação concluída", identity.primeira_orientacao),
            ("ID Lattes", identity.lattes_id),
            ("URL Lattes", identity.lattes_url),
            ("Atualização do Lattes", identity.atualizacao_lattes),
            ("URL/caminho do perfil", identity.perfil_url),
            ("Fonte dos dados", data.arquivo_origem),
        ]
        for row in identity_rows:
            self.identity_tree.insert("", "end", values=row)

        self._clear_tree(self.metrics_tree)
        for code in METRIC_ORDER:
            self.metrics_tree.insert("", "end", values=(code, METRIC_LABELS[code], data.metricas.get(code, "")))

        self.refresh_positioning()
        self._update_summary()

    def refresh_positioning(self) -> None:
        if not hasattr(self, "position_tree"):
            return
        self._clear_tree(self.position_tree)
        if not self.profile_data:
            return
        for item in self.profile_data.posicionamentos:
            self.position_tree.insert(
                "",
                "end",
                values=(
                    item.codigo,
                    item.metrica,
                    item.valor,
                    self._format_percentile(item.entre_doutores_orientadores),
                    self._format_percentile(item.mesmo_ano_primeira_orientacao),
                    self._format_percentile(item.mesma_grande_area),
                    self._format_percentile(item.mesma_area),
                ),
            )

    def _format_percentile(self, value: Optional[float]) -> str:
        if value is None:
            return ""
        if self.round_percentiles.get():
            return f"{round(value)}%"
        return f"{value:.2f}%".replace(".", ",")

    def _update_summary(self) -> None:
        if not self.profile_data:
            return
        data = self.profile_data
        identity = data.identidade
        metrics_text = "; ".join(f"{code} = {data.metricas.get(code, '')}" for code in METRIC_ORDER)
        paragraphs = [
            f"{identity.nome or 'O pesquisador'} está vinculado(a) a "
            f"{identity.instituicao or 'instituição não identificada'}, na grande área "
            f"{identity.grande_area or 'não identificada'} e na área {identity.area or 'não identificada'}. "
            f"A primeira orientação concluída registrada ocorreu em "
            f"{identity.primeira_orientacao or 'ano não identificado'}. O ID Lattes informado é "
            f"{identity.lattes_id or 'não localizado'}, com atualização em "
            f"{identity.atualizacao_lattes or 'data não localizada'}.",
            f"Métricas topológicas: {metrics_text}.",
        ]
        if data.posicionamentos:
            paragraphs.append("Posicionamento percentílico por métrica:")
            for item in data.posicionamentos:
                paragraphs.append(
                    f"• {item.metrica} ({item.codigo}), valor {item.valor}: "
                    f"{self._percent_text(item.entre_doutores_orientadores)} entre doutores orientadores; "
                    f"{self._percent_text(item.mesmo_ano_primeira_orientacao)} no mesmo ano da primeira orientação; "
                    f"{self._percent_text(item.mesma_grande_area)} na grande área; "
                    f"{self._percent_text(item.mesma_area)} na área."
                )
        if data.avisos:
            paragraphs.append("Avisos de extração:\n" + "\n".join(f"• {warning}" for warning in data.avisos))

        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", "\n\n".join(paragraphs))
        self.summary_text.configure(state="disabled")

    @staticmethod
    def _percent_text(value: Optional[float]) -> str:
        return "não disponível" if value is None else f"percentil {value:.2f}".replace(".", ",")

    # ------------------------------------------------------------------
    # Exportações individuais
    # ------------------------------------------------------------------
    def export_json(self) -> None:
        if not self._ensure_profile_data():
            return
        assert self.profile_data is not None
        path = filedialog.asksaveasfilename(
            title="Exportar perfil em JSON",
            defaultextension=".json",
            initialfile=self._default_export_name("json"),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps(asdict(self.profile_data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.status.set(f"JSON exportado: {path}")
        messagebox.showinfo("Exportação concluída", "Arquivo JSON salvo com sucesso.")

    def export_csv(self) -> None:
        if not self._ensure_profile_data():
            return
        assert self.profile_data is not None
        path = filedialog.asksaveasfilename(
            title="Exportar perfil em CSV",
            defaultextension=".csv",
            initialfile=self._default_export_name("csv"),
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        data = self.profile_data
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["SEÇÃO", "CAMPO", "CÓDIGO", "VALOR", "GLOBAL", "MESMO_ANO", "GRANDE_ÁREA", "ÁREA"])
            for key, value in asdict(data.identidade).items():
                writer.writerow(["Identidade acadêmica", key, "", value, "", "", "", ""])
            for code in METRIC_ORDER:
                writer.writerow(["Métricas topológicas", METRIC_LABELS[code], code, data.metricas.get(code, ""), "", "", "", ""])
            for item in data.posicionamentos:
                writer.writerow([
                    "Posicionamento",
                    item.metrica,
                    item.codigo,
                    item.valor,
                    self._csv_number(item.entre_doutores_orientadores),
                    self._csv_number(item.mesmo_ano_primeira_orientacao),
                    self._csv_number(item.mesma_grande_area),
                    self._csv_number(item.mesma_area),
                ])
        self.status.set(f"CSV exportado: {path}")
        messagebox.showinfo("Exportação concluída", "Arquivo CSV salvo com sucesso.")

    # ------------------------------------------------------------------
    # Exportações consolidadas
    # ------------------------------------------------------------------
    def export_batch_csv(self) -> None:
        if not self._ensure_batch_results():
            return
        path = filedialog.asksaveasfilename(
            title="Exportar lote consolidado em CSV",
            defaultextension=".csv",
            initialfile="perfis_acacia_consolidados.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        headers = self._batch_export_headers()
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, delimiter=";", extrasaction="ignore")
            writer.writeheader()
            for result in self.batch_results:
                writer.writerow(self._flatten_batch_result(result))
        self.status.set(f"Lote CSV exportado: {path}")
        messagebox.showinfo("Exportação concluída", "A base consolidada foi salva com sucesso.")

    def export_batch_json(self) -> None:
        if not self._ensure_batch_results():
            return
        path = filedialog.asksaveasfilename(
            title="Exportar lote em JSON",
            defaultextension=".json",
            initialfile="perfis_acacia_consolidados.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        payload = []
        for result in self.batch_results:
            payload.append(
                {
                    "entrada": asdict(result.entrada),
                    "status": result.status,
                    "erro": result.erro,
                    "perfil": asdict(result.perfil) if result.perfil else None,
                }
            )
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.set(f"Lote JSON exportado: {path}")
        messagebox.showinfo("Exportação concluída", "O lote em JSON foi salvo com sucesso.")

    @staticmethod
    def _batch_export_headers() -> list[str]:
        headers = [
            "ordem",
            "nome_informado",
            "url_informada",
            "slug",
            "url_consulta",
            "status",
            "erro",
            "nome_perfil",
            "grande_area",
            "area",
            "instituicao",
            "primeira_orientacao",
            "lattes_id",
            "lattes_url",
            "atualizacao_lattes",
            "perfil_url_final",
            "avisos",
        ]
        headers.extend(METRIC_ORDER)
        for code in METRIC_ORDER:
            headers.extend([
                f"{code}_percentil_global",
                f"{code}_percentil_mesmo_ano",
                f"{code}_percentil_grande_area",
                f"{code}_percentil_area",
            ])
        return headers

    def _flatten_batch_result(self, result: BatchResult) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ordem": result.entrada.ordem,
            "nome_informado": result.entrada.nome_informado,
            "url_informada": result.entrada.url_informada,
            "slug": result.entrada.slug,
            "url_consulta": result.entrada.url_consulta,
            "status": result.status,
            "erro": result.erro,
        }
        if not result.perfil:
            return row

        profile = result.perfil
        identity = profile.identidade
        row.update({
            "nome_perfil": identity.nome,
            "grande_area": identity.grande_area,
            "area": identity.area,
            "instituicao": identity.instituicao,
            "primeira_orientacao": identity.primeira_orientacao,
            "lattes_id": identity.lattes_id,
            "lattes_url": identity.lattes_url,
            "atualizacao_lattes": identity.atualizacao_lattes,
            "perfil_url_final": identity.perfil_url,
            "avisos": " | ".join(profile.avisos),
        })
        for code in METRIC_ORDER:
            row[code] = profile.metricas.get(code, "")
        positioning_by_code = {item.codigo: item for item in profile.posicionamentos}
        for code in METRIC_ORDER:
            item = positioning_by_code.get(code)
            if not item:
                continue
            row[f"{code}_percentil_global"] = self._csv_number(item.entre_doutores_orientadores)
            row[f"{code}_percentil_mesmo_ano"] = self._csv_number(item.mesmo_ano_primeira_orientacao)
            row[f"{code}_percentil_grande_area"] = self._csv_number(item.mesma_grande_area)
            row[f"{code}_percentil_area"] = self._csv_number(item.mesma_area)
        return row

    @staticmethod
    def _csv_number(value: Optional[float]) -> str:
        return "" if value is None else f"{value:.6f}".replace(".", ",")

    def _default_export_name(self, extension: str) -> str:
        if not self.profile_data:
            return f"perfil_acacia.{extension}"
        name = self.profile_data.identidade.nome or "perfil_acacia"
        slug = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_").lower()
        return f"{slug}_acacia.{extension}"

    def _ensure_profile_data(self) -> bool:
        if not self.profile_data:
            messagebox.showwarning("Nenhum perfil", "Busque ou abra primeiro um perfil.")
            return False
        return True

    def _ensure_batch_results(self) -> bool:
        if not self.batch_results:
            messagebox.showwarning("Nenhum lote", "Importe e processe primeiro uma lista CSV.")
            return False
        if self.is_batch_loading:
            messagebox.showwarning("Lote em processamento", "Aguarde o término ou interrompa o lote antes de exportar.")
            return False
        return True

    # ------------------------------------------------------------------
    # Limpeza e encerramento
    # ------------------------------------------------------------------
    def clear_profile(self) -> None:
        if self.is_loading:
            return
        self.profile_data = None
        self.slug_var.set("")
        if self.batch_source_file:
            self.current_file.set(self.batch_source_file)
        else:
            self.current_file.set("Nenhuma fonte carregada")
        self.status.set("Perfil ativo limpo.")
        for tree in (self.identity_tree, self.metrics_tree, self.position_tree):
            self._clear_tree(tree)
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.configure(state="disabled")
        self.slug_entry.focus_set()

    def clear_batch(self) -> None:
        if self.is_batch_loading:
            return
        self.batch_inputs = []
        self.batch_results = []
        self.batch_source_file = ""
        self._clear_tree(self.batch_tree)
        self.batch_progress.configure(maximum=1, value=0)
        self.batch_status.set("Nenhum lote carregado.")
        self.export_batch_csv_button.configure(state="disabled")
        self.export_batch_json_button.configure(state="disabled")
        self.clear_batch_button.configure(state="disabled")
        self.current_file.set(self.profile_data.arquivo_origem if self.profile_data else "Nenhuma fonte carregada")
        self.status.set("Lote removido.")

    def _on_close(self) -> None:
        if self.is_batch_loading:
            self.batch_stop_event.set()
        self.destroy()

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)


def main() -> None:
    app = AcaciaExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
