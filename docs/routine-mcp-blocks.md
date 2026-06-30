# Routine prompt — blocchi in modalità MCP (drop-in)

Sostituiscono, nel prompt della Cloud Routine (v3.1), i blocchi **Bootstrap**,
**Read inbox + state + template**, **Commit brief/archive/state/index**,
**Generate index.html**, **Outbox notify** e **Cleanup inbox**. Tutta la I/O su
GitHub passa dai tool `mcp__github__*`: **nessun `exec()` di `briefs_lib`,
nessun `GH_TOKEN`**.

Costanti:
- `owner = dandreagabriele-sudo`, `repo = radiolearn-briefs`, `branch/ref = main`
- `SITE = https://dandreagabriele-sudo.github.io/radiolearn-briefs`

> Nota: `get_file_contents` restituisce anche lo SHA del file, ma `push_files`
> non lo richiede (ricostruisce l'albero dal HEAD e sovrascrive). Quindi lo SHA
> si può ignorare.

---

## Bootstrap (MCP)

In modalità MCP **non c'è bootstrap Python**. La routine non scarica né esegue
`briefs_lib.py` e non usa alcun token. Procede direttamente con i tool MCP.

## Read inbox + state + template (MCP)

```
# 1) inbox
mcp__github__get_file_contents(owner, repo, path="inbox/candidates.json", ref="main")
#    → se 404/assente: "No candidates.json in inbox. Exiting." e TERMINA
#    → parse JSON; se NOW - staged_at > 60 min: stale, TERMINA

# 2) stato (da riscrivere aggiornato dopo la curation)
mcp__github__get_file_contents(owner, repo, path="state.json", ref="main")
#    → se assente, usa il default {version, last_brief_at:null, processed_dois:[], briefs_archive:[], ...}

# 3) template
mcp__github__get_file_contents(owner, repo, path="templates/brief_template.html", ref="main")
```

## Curation + generazione artefatti

Invariata (lavoro editoriale dell'agent). Costruisci **in-context** — o con uno
script Python locale puramente computazionale, **senza chiamate di rete a
GitHub** — i quattro artefatti del sito:
- `briefs/<week_iso>.html` (template riempito),
- `archive/<week_iso>.json`,
- `state.json` aggiornato,
- `index.html` rigenerato.

`state.json` aggiornato deve contenere:
- `last_brief_at` = NOW (ISO 8601)
- `last_brief_url` = `SITE + "/briefs/<week_iso>.html"`
- `last_brief_item_count` = n. paper selezionati
- `processed_dois` += DOI selezionati (dedup, lowercase-safe)
- `briefs_archive` += `{id, url, date, items, headline}`

`index.html`: rigenera l'intero file dai `briefs_archive` (ordina per `date`
desc), stesso formato del file esistente (header + `<ul>` di `<li>` con
link/data/`N items` + footer).

## Commit brief + archive + state + index (un solo commit, MCP)

```
mcp__github__push_files(
  owner, repo, branch="main",
  message="Brief <week_iso>: <headline>",
  files=[
    {path: "briefs/<week_iso>.html", content: <brief html>},
    {path: "archive/<week_iso>.json", content: <archive json>},
    {path: "state.json",             content: <state json aggiornato>},
    {path: "index.html",             content: <index rigenerato>},
  ])
```

`push_files` è atomico e **non richiede SHA**; sovrascrive i file esistenti
(utile anche per i re-run dello stesso `week_iso`).

## Outbox notify (commit separato → trigger Telegram, MCP)

```
mcp__github__create_or_update_file(
  owner, repo, branch="main",
  path="outbox/brief-<week_iso>.json",
  message="Outbox: notify <week_iso> via Telegram",
  content = {
    "brief_id":   "<week_iso>",
    "created_at": "<NOW iso>",
    "messages": [
      {"method": "sendMessage",
       "params": {"chat_id": "8538175163",
                  "text": "<testo notifica>",
                  "disable_web_page_preview": false}}
    ]
  })
```

- `chat_id` è lo stesso di `send.py`/`deliver_outbox.py` (`8538175163`).
- File nuovo → **niente SHA**. Il commit su `outbox/*.json` fa partire
  `send-to-telegram-briefs.yml`, che consegna il messaggio e **drena** l'outbox
  (cancellando il file). La drain dell'outbox è il segnale di consegna avvenuta.
- Re-run stesso `week_iso`: se `outbox/brief-<week_iso>.json` esiste ancora,
  prima fai `get_file_contents` per ottenerne lo SHA e passalo a
  `create_or_update_file`, oppure `delete_file` e ricrealo.

## Cleanup inbox (MCP)

```
mcp__github__delete_file(
  owner, repo, branch="main",
  path="inbox/candidates.json",
  message="Cleanup inbox after <week_iso>")
```

`delete_file` **non richiede SHA**.

---

## Mapping rapido (per riferimento)

| Vecchio (REST, `briefs_lib`) | Nuovo (MCP) |
|---|---|
| `gh_get(path)` | `get_file_contents(path, ref="main")` |
| `gh_put` ×N (brief+archive+state+index) | `push_files(files=[...])` |
| `gh_put` (singolo, file esistente) | `create_or_update_file(..., sha=<da get_file_contents>)` |
| `gh_put` (outbox, file nuovo) | `create_or_update_file(...)` senza SHA |
| `gh_delete(path, sha, msg)` | `delete_file(path, message, branch)` (no SHA) |
| `init_briefs` + `GH_TOKEN` | — (rimossi; auth gestita dall'host MCP) |
