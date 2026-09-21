"""The same assertions execute on native Pyodide and transformed MicroPython."""


def render(value):
    return f"""before {("" if value else f"""nested {value}""")} after"""


def validate():
    assert render(True) == 'before  after'
    assert render(False) == 'before nested False after'
    value = True
    assert f"{f'{value=}'}" == 'value=True'
    assert [f"<{f'item {value}'}>" for value in range(3)] == ['<item 0>', '<item 1>', '<item 2>']
    assert f"{f'{{brace}}\nline'}" == '{brace}\nline'
    width, precision, number = 8, 2, 3.14159
    assert f"[{f'{number:{width}.{precision}f}'}]" == '[    3.14]'
    assert f"{42:{f'0{4}d'}}" == '0042'
    assert f"{f'{True:04d}'}" == '0001'
    assert f"{f'{False:04d}'}" == '0000'
    text = 'café ☃ 😀'
    assert f"{f'{text!s}'}" == text
    assert f"{f'{text!r}'}" == "'café ☃ 😀'"
    assert f"{f'{text!a}'}" == "'caf\\xe9 \\u2603 \\U0001f600'"
    events = []

    class Item:
        def __repr__(self):
            events.append('repr')
            return 'tag'

        def __format__(self, spec):
            events.append('format:' + spec)
            return 'custom'

    def make():
        events.append('value')
        return Item()

    def spec():
        events.append('spec')
        return '>5'

    assert f"[{f'{make()!r:{spec()}}'}]" == '[  tag]'
    assert events == ['value', 'repr', 'spec']
    events.clear()
    assert f"[{f'{make():{spec()}}'}]" == '[custom]'
    assert events == ['value', 'spec', 'format:>5']
    events.clear()

    def tick(label):
        events.append(label)
        return label

    def fail():
        events.append('failure')
        raise ValueError('expected failure')

    choose = True
    assert f"{('safe' if choose else f'{fail()}')}" == 'safe'
    assert not events
    assert f"{f'{tick('a')}'} {f'{tick('b')}'}" == 'a b'
    assert events == ['a', 'b']
    events.clear()
    try:
        f"{f'{fail()}'} {tick('must-not-run')}"
    except ValueError:
        pass
    else:
        raise AssertionError('formatting must propagate failures')
    assert events == ['failure']

    class Invalid:
        def __format__(self, spec):
            return 42

    try:
        f"{f'{Invalid()}'}"
    except TypeError:
        pass
    else:
        raise AssertionError('non-string __format__ result must fail')


async def validate_async():
    events = []

    async def value():
        events.append('awaited')
        return 'async'

    assert f"[{f'{await value()}'}]" == '[async]'
    assert events == ['awaited']
