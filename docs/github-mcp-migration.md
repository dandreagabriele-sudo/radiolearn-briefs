# Migrazione GitHub: REST API → GitHub MCP (per la Cloud Routine)

## Perché

Con il cambio di policy del proxy Anthropic, le chiamate HTTPS in uscita dalla
**Cloud Routine** verso la REST API di GitHub (`api.github.com`) e altri host
possono ricevere `403 Forbidden` a livello di proxy. La routine gira in un
ambiente di esecuzione proxato: quando il proxy blocca `api.github.com`, gli
helper REST (`gh_get`/`gh_put`/`gh_delete`/`gh_list` di `briefs_lib.py`, basati
su `urllib`) falliscono e il brief non viene pubblicato.

La soluzione è far svolgere alla routine la propria I/O su GitHub tramite i
**tool GitHub MCP** (`mcp__github__*`), che sono autenticati dall'host ed esulano
dal percorso proxato che restituisce 403.

## Ambito: cosa migra e cosa NO

Questo è il punto cruciale. I `gh_*` di `briefs_lib.py` hanno **tre** consumatori
con contesti di esecuzione diversi:

| Componente | Dove gira | Tocca il proxy Anthropic? | Migrazione |
|---|---|---|---|
| **Cloud Routine** (curation + pubblicazione brief) | Ambiente agent proxato | **Sì** → rischio 403 | **Sì → GitHub MCP** |
| `fetch_sources.py` | GitHub Actions runner | No | **No — resta REST** |
| `deliver_outbox.py` | GitHub Actions runner | No | **No — resta REST** |

Due conseguenze, entrambe non negoziabili:

1. **I tool MCP non sono richiamabili da Python.** Sono tool-call dell'agent, non
   una libreria. Quindi non si può "convertire `briefs_lib.py` a MCP": gli helper
   REST restano, perché servono agli script che girano su GitHub Actions.
2. **Gli script Actions non vanno migrati.** Girano sui runner GitHub, che NON
   passano dal proxy Anthropic: per loro `api.github.com` funziona sempre.
   Migrarli a MCP è impossibile (niente MCP su un runner) e inutile.

> In breve: **la migrazione vive nel prompt/config della routine**, non nel
> codice Python di questo repo. Questo documento è la specifica autorevole che
> il prompt della routine deve seguire.

## Mappatura REST → MCP

| Helper REST (`briefs_lib`) | Scopo | Sostituto MCP |
|---|---|---|
| `gh_get(path)` → `(content, sha)` | leggere file + SHA | `mcp__github__get_file_contents(owner, repo, path, ref="main")` |
| `gh_list(folder)` | elencare cartella | `mcp__github__get_file_contents(owner, repo, path=folder)` |
| `gh_put(path, content, msg, sha)` | creare/aggiornare 1 file | `mcp__github__create_or_update_file(...)` — `sha` obbligatorio se il file esiste |
| `gh_put` ×N (commit multi-file) | pubblicare il brief in blocco | `mcp__github__push_files(owner, repo, branch, files[], message)` — commit atomico, **nessun SHA** |
| `gh_delete(path, sha, msg)` | cancellare file | `mcp__github__delete_file(owner, repo, path, message, branch)` — **nessun SHA** |

Note operative:

- `create_or_update_file` **richiede `sha`** per aggiornare un file esistente
  (es. `state.json`, `index.html`). Lo SHA si ottiene da `get_file_contents`.
- `push_files` ricostruisce l'albero dal HEAD del branch e committa: **non serve
  SHA** e aggiorna più file in un solo commit. È la via più semplice per
  pubblicare il brief.
- `delete_file` **non** richiede SHA (a differenza di `gh_delete`).
- `owner = "dandreagabriele-sudo"`, `repo = "radiolearn-briefs"`, `branch = "main"`.

## Procedura della routine in modalità MCP

Sostituisce il blocco *Bootstrap* + *Read inbox* + i blocchi *Commit/Outbox/Cleanup*
del prompt v3.1. **In modalità MCP la routine non fa più `exec()` di `briefs_lib`
e non usa più `GH_TOKEN`.**

> I blocchi del prompt riscritti e pronti da incollare sono in
> [`routine-mcp-blocks.md`](routine-mcp-blocks.md). Il **prompt completo della
> routine** già in modalità MCP (v3.2, senza `GH_TOKEN`), da incollare interamente
> nella config, è in [`routine-prompt-mcp.md`](routine-prompt-mcp.md).

1. **Leggi inbox** — `get_file_contents(path="inbox/candidates.json", ref="main")`.
   Se assente o `staged_at` più vecchio di 60 min → esci (come oggi).
2. **Leggi stato** — `get_file_contents(path="state.json")`; conserva il `sha`
   restituito (serve solo se aggiorni via `create_or_update_file`; con
   `push_files` non serve).
3. **Leggi template** — `get_file_contents(path="templates/brief_template.html")`.
4. **Curation + generazione HTML** — invariata (lavoro editoriale dell'agent).
5. **Pubblica il brief** in un solo commit:
   `push_files(branch="main", message="Brief <week>: <headline>", files=[`
   `  {path:"briefs/<week>.html", content: <html>},`
   `  {path:"archive/<week>.json", content: <archive json>},`
   `  {path:"state.json", content: <state json aggiornato>},`
   `  {path:"index.html", content: <index rigenerato>}])`.
6. **Notifica Telegram** — `create_or_update_file(path="outbox/brief-<week>.json",`
   `content=<payload messaggi>, message="Outbox: notify <week>", branch="main")`.
   Commit separato così il workflow `send-to-telegram-briefs.yml` parte pulito
   sul path `outbox/*.json`.
7. **Pulisci inbox** — `delete_file(path="inbox/candidates.json",`
   `message="Cleanup inbox after <week>", branch="main")`.

Il formato di `outbox/<id>.json` resta invariato (vedi `deliver_outbox.py`):
`{"messages": [{"method": "sendMessage", "params": {"chat_id": "...", "text": "..."}}]}`.

## Beneficio di sicurezza: via il PAT dal prompt

Oggi il prompt della routine contiene un Personal Access Token GitHub **in chiaro**
(`GH_TOKEN`). In modalità MCP la routine **non ne ha più bisogno**: l'autenticazione
GitHub è gestita dall'host MCP. Azioni raccomandate:

1. Rimuovere `GH_TOKEN` dal prompt/bootstrap della routine.
2. **Ruotare** il PAT attualmente esposto (è stato in chiaro nel prompt): da
   GitHub → *Settings → Developer settings → Fine-grained tokens* → revoca e
   rigenera.
3. Aggiornare il secret `GH_PAT` di Actions con il nuovo token (gli script
   Actions continuano a usarlo via REST: è un secret, non finisce nel prompt).

## Cosa NON cambia

- `fetch_sources.py`, `deliver_outbox.py`, `briefs_lib.py`: invariati. Girano su
  Actions (no proxy) e devono restare REST.
- Il formato di `candidates.json`, `state.json`, `archive/*.json`, `outbox/*.json`.
- La cadenza, la cura editoriale e la struttura del brief.
