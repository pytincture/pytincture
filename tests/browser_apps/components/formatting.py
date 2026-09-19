from dataclasses import dataclass, field


@dataclass
class Identity:
    name: str


@dataclass
class Display(Identity):
    labels: list = field(default_factory=list)


def heading(result):
    first = Display('Warehouse')
    second = Display('Other')
    first.labels.append('ready')
    assert second.labels == []
    return first.name + ': ' + result['sku'] + ' / ' + str(result['limit'])
