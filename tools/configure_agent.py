"""Create repository agent configuration; never reads runtime credentials."""
import json
import os
import shlex
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
skills={
'history-invariants': '''---
name: history-invariants
description: Реализовать или проверить историю объявлений, неполные прогоны, снятие и перевыставление. Не применять к настройкам Telegram и оформлению API.
---
Разделять объявление, машину, версию и прогон. Хранить исходные наблюдения неизменяемо.
Снятие разрешать только после полного прогона, покрытия >=90% последнего полного склада и отдельного подтверждения отсутствия. На сбое подтверждения оставлять статус.
Связывать уверенно по полному VIN, частичный не уникален. Фото сравнивать по содержимому, не URL. Эвристические связи помечать вероятными.
Проверить возврат, новый ID, обе цены, описание, фото, комплектацию, повторное наблюдение и откат транзакции. Не называть снятие продажей.
''',
'moderated-outbox': '''---
name: moderated-outbox
description: Реализовать или проверить Telegram-очередь, модерацию, повторные кнопки и восстановление после ошибок. Не применять к сборщику источника или отчётам по складу.
---
Сохранять пост до сетевого запроса. Проверять группу и конкретного модератора, переход состояния делать атомарно. MCP только предлагает.
Тестовый канал по умолчанию; боевой включает человек локально. Агент не меняет этот режим.
Повторная кнопка и перезапуск не создают вторую отправку. Не повторять слепо запрос после таймаута: Telegram не даёт общего ключа идемпотентности. Сохранять unknown/uncertain для сверки человеком.
Явный 429 учитывает retry_after. Ошибка сохраняет пост. Альбом <=10 фото, caption <=1024, текст <=4096. Тестировать fake Bot API и перезапуск.
'''}
MCP_ARGS=['compose','exec','-T','api','python','-m','kiravto','mcp']

def hook(script,project_root=root,python=None):
    python=Path(python or sys.executable).as_posix()
    path=(Path(project_root)/script).as_posix()
    if os.name=='nt':
        quoted_python=python.replace("'","''");quoted_path=path.replace("'","''")
        command=f'powershell.exe -NoProfile -NonInteractive -Command "& \'{quoted_python}\' -X utf8 \'{quoted_path}\'"'
    else:command=shlex.join([python,'-X','utf8',path])
    return {'type':'command','command':command,'timeout':10}

def configure(project_root=root):
    project_root=Path(project_root)
    for name,content in skills.items():
        path=project_root/'.agents'/'skills'/name/'SKILL.md'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(content,encoding='utf-8')
    folder=project_root/'.codex';folder.mkdir(exist_ok=True)
    hooks={'PreToolUse':[{'matcher':'.*','hooks':[hook('hooks/protect.py',project_root)]}],'SessionStart':[{'hooks':[hook('hooks/session_status.py',project_root)]}]}
    (folder/'hooks.json').write_text(json.dumps({'hooks':hooks},ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'config.toml').write_text('[features]\nhooks = true\n\n[mcp_servers.kiravto]\ncommand = "docker"\nargs = '+json.dumps(MCP_ARGS)+'\ncwd = '+json.dumps(project_root.as_posix())+'\n',encoding='utf-8')
    agents=folder/'agents';agents.mkdir(exist_ok=True)
    (agents/'verifier.md').write_text('# Независимый сверщик\nКод не менять. Не читать .env и не отправлять сообщения. Проверять 20 случайных машин по источнику, фиксировать seed и время, каждое расхождение и неизвестное. Для кода проверять diff и доменные инварианты. Не создавать своих агентов. Вывод основному агенту для перепроверки.\n',encoding='utf-8')
    (project_root/'.mcp.json').write_text(json.dumps({'mcpServers':{'kiravto':{'command':'docker','args':MCP_ARGS,'cwd':project_root.as_posix()}}},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    configure()
    print('Project agent configuration written. Review hooks in a trusted session; existing credentials were not accessed.')
