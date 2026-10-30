from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    columns: list[Column]
    description: str | None = None


@dataclass(frozen=True)
class Schema:
    """What the agent knows about the data: a curated subset of the real tables."""

    dataset: str
    tables: list[Table]

    def render(self) -> str:
        """Render as a compact text block for the system prompt."""
        blocks = []
        for table in self.tables:
            header = f"Table `{self.dataset}.{table.name}`"
            if table.description:
                header += f": {table.description}"
            lines = [header]
            for column in table.columns:
                line = f"- {column.name} {column.type}"
                if column.description:
                    line += f": {column.description}"
                lines.append(line)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


def load_schema(path: Path) -> Schema:
    raw = yaml.safe_load(path.read_text())
    tables = [
        Table(
            name=table_name,
            description=table.get("description"),
            columns=[
                Column(name=column_name, type=column["type"], description=column.get("description"))
                for column_name, column in table["columns"].items()
            ],
        )
        for table_name, table in raw["tables"].items()
    ]
    return Schema(dataset=raw["dataset"], tables=tables)
