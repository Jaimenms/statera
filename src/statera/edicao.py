"""Uma edição em execução: o contexto que as tarefas do processo compartilham.

O estado é gravado em `<produto>/8-evaluate/data/execucoes/<competencia>.estado.json` a cada
tarefa, para que o processo possa parar numa tarefa humana e continuar depois
(`statera resume`), inclusive em outra máquina ou outro dia.

Toda tarefa automática é uma função `etapa(ed: Edicao) -> str` que devolve a SITUAÇÃO
("ok" ou "failed"); é a situação que decide o caminho nos gateways do processo.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from statera.projeto import Projeto, Unidade, atual


class FalhaNaEdicao(RuntimeError):
    """Uma tarefa não pôde ser concluída (ex.: edição já publicada, gate de classificação)."""


def agora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Edicao:
    produto: str
    competencia: str
    data_corte: str
    forcar: bool = False
    situacao: str = "running"  # em-execucao | aguardando | barrada | concluida
    pendente: str | None = None  # id da tarefa humana à espera de conclusão
    run_id: str = ""  # execução OpenLineage da edição (pai das execuções das tarefas)
    extracoes: list[dict] = field(default_factory=list)
    resultados_dbt: dict = field(default_factory=dict)  # testes, falhas, nós executados
    verificacoes: int = 0  # verificações de contrato executadas
    falhas_nao_bloqueantes: int = 0
    falhas: list[str] = field(default_factory=list)  # falhas que barram (dbt, contratos, controles)
    revisoes: list[dict] | None = None  # resultado do controle de revisão entre edições
    publicacao: dict = field(default_factory=dict)  # pasta, versão, retificação, supressões
    cortes: dict = field(default_factory=dict)  # manifestos dos cortes fixados dos produtos internos de entrada
    historico: list[dict] = field(default_factory=list)  # tarefas concluídas, com situação
    ctx: dict = field(default_factory=dict, repr=False)  # contexto OpenLineage da tarefa corrente

    # --- acesso ao projeto ------------------------------------------------------------------
    @property
    def projeto(self) -> Projeto:
        return atual()

    @property
    def unidade(self) -> Unidade:
        return self.projeto.produto(self.produto)

    @property
    def rotulo(self) -> str:
        """'Edição novo-caged 2026-05' ou, num produto interno, 'Corte vinculos-emprego 2026-07-20'."""
        return (
            f"Corte {self.produto} {self.data_corte}"
            if self.unidade.interno
            else f"Edição {self.produto} {self.competencia}"
        )

    @property
    def faceta(self) -> dict:
        return {"product": self.produto, "period": self.competencia, "cutDate": self.data_corte}

    @property
    def pai(self) -> dict:
        return {"run_id": self.run_id, "job": f"{self.produto}.edicao"}

    @property
    def arquivo_eventos(self) -> Path:
        return self.unidade.pasta_execucoes / "eventos.jsonl"

    def pasta_dados(self, fase: int) -> Path:
        """Pasta da edição dentro dos dados da fase: <produto>/<fase>/data/<competência>/."""
        return self.unidade.dados(fase) / self.competencia

    # --- registro ---------------------------------------------------------------------------
    def log(self, mensagem: str) -> None:
        print(f"[statera] {mensagem}", flush=True)

    def registrar_tarefa(self, tarefa: str, situacao: str, duracao_s: float | None = None, **extra) -> None:
        """Uma linha por tarefa em execucoes.jsonl (insumo do indicador de duração, fase 8)."""
        linha = {
            **self.faceta,
            "tarefa_bpmn": tarefa,
            "run_id": self.ctx.get("run_id", ""),
            "duracao_s": duracao_s,
            "status": situacao,
            "registered_at": agora(),
            **extra,
        }
        self.historico.append({"tarefa": tarefa, "status": situacao, **extra})
        destino = self.unidade.pasta_execucoes / "execucoes.jsonl"
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")

    # --- persistência -----------------------------------------------------------------------
    @staticmethod
    def arquivo_estado(unidade: Unidade, competencia: str) -> Path:
        return unidade.pasta_execucoes / f"{competencia}.estado.json"

    def salvar(self) -> Path:
        destino = self.arquivo_estado(self.unidade, self.competencia)
        destino.parent.mkdir(parents=True, exist_ok=True)
        dados = {k: v for k, v in asdict(self).items() if k != "ctx"}
        destino.write_text(json.dumps(dados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return destino

    @classmethod
    def carregar(cls, unidade: Unidade, competencia: str) -> Edicao:
        caminho = cls.arquivo_estado(unidade, competencia)
        if not caminho.exists():
            raise FalhaNaEdicao(f"Não há execução de {unidade.nome} {competencia} para continuar ({caminho})")
        return cls(**json.loads(caminho.read_text(encoding="utf-8")))


def ler_jsonl(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]
