# Interval Training Dashboard

Dashboard locale per analizzare gli allenamenti di corsa con ripetute a partire dai file FIT di Garmin. Tutto gira sul tuo PC: nessun cloud, nessun database. Calcola KPI avanzati (EF, Pace Fade, HRR60, TRIMP, ACWR…) e li presenta in un'interfaccia interattiva per analisi della sessione, confronto storico e monitoraggio del carico settimanale.

## Requisiti

- Python 3.11+

## Installazione

```bash
pip install -r requirements.txt
```

(oppure: `pip install flask fit-tool garminconnect python-dotenv anthropic`)

## Avvio

```bash
python app.py
```

Apri il browser su **http://localhost:5000**. Alla prima apertura la dashboard è vuota: importa qualche sessione.

## Configurazione (.env)

Copia il template e inserisci i tuoi valori:

```bash
cp .env.example .env
```

- `ANTHROPIC_API_KEY` — abilita la chat AI nella schermata di dettaglio sessione. Senza chiave la chat mostra "AI not configured". La chiave resta **solo lato server**, mai nel browser.
- `GARMIN_EMAIL` / `GARMIN_PASSWORD` — servono solo per `garmin_sync.py`.

## Come ottenere i file FIT da Garmin

### 1. Esportazione manuale (sempre funziona, nessuna configurazione)

1. [connect.garmin.com](https://connect.garmin.com) → **Attività** → apri un'attività
2. Icona ingranaggio (⚙) in alto a destra → **Esporta originale**
3. Estrai lo `.zip` scaricato → all'interno trovi il file `.fit`
4. Trascinalo nella dashboard (**Import FIT**) oppure copialo in `fit_files/` e premi **Scan fit_files/**

**Esportazione completo dello storico:** impostazioni account Garmin → **Gestione dati → Esporta i tuoi dati**. Garmin invia via email un link a uno ZIP con la cartella `Activities/` contenente tutti i FIT registrati. Estrai tutto in `fit_files/` e premi **Scan**. È il modo consigliato per importare mesi di storico in una volta.

### 2. Download automatico — `garmin_sync.py`

Scarica i FIT direttamente da Garmin Connect usando la libreria non ufficiale `garminconnect`. Passo per passo:

**Passo 1 — credenziali nel file `.env`** (mai sulla riga di comando):

```
GARMIN_EMAIL=tua@email.com
GARMIN_PASSWORD=la-tua-password
```

**Passo 2 — primo avvio.** Esegui lo script. Al primo login lo script si autentica con email e password; se sul tuo account è attiva la **verifica in due passaggi (2FA)**, lo script chiede il codice OTP direttamente nel terminale:

```
$ python garmin_sync.py
Garmin 2FA code: 123456
```

A login riuscito i token di sessione vengono salvati in `.garmin_session/` (permessi `600`). **Agli avvii successivi non servono più password né OTP**: lo script riusa quella sessione finché resta valida.

**Passo 3 — scegli cosa scaricare** (default: ultimi 30 giorni, solo corsa):

```bash
python garmin_sync.py                          # ultimi 30 giorni
python garmin_sync.py --days 60                # ultimi 60 giorni
python garmin_sync.py --from 2026-04-01 --to 2026-05-31   # intervallo di date
python garmin_sync.py --limit 10               # ultime 10 attività
python garmin_sync.py --all-types              # include attività non di corsa
python garmin_sync.py --output altra_cartella  # salva altrove (default: fit_files/)
```

Lo script salta i file già presenti (riconosce l'ID attività nel nome), gestisce il limite di richieste di Garmin (attesa di 60 s e un nuovo tentativo) e a fine esecuzione stampa un riepilogo: scaricati / saltati / falliti.

**Passo 4 — importa nella dashboard.** I file finiscono in `fit_files/`. Apri la dashboard e premi **Scan fit_files/** nella barra laterale: un toast mostra quanti file sono stati aggiunti, saltati (duplicati o non-corsa) o in errore.

> **Risoluzione problemi.** È un'API non ufficiale e può cambiare. Se il login fallisce di continuo: verifica email/password in `.env`; cancella la cartella `.garmin_session/` per forzare un nuovo login; controlla che il tuo account non sia bloccato su [sso.garmin.com](https://sso.garmin.com). Se nulla funziona, usa sempre l'**esportazione manuale** (sezione 1) come ripiego.

### 3. Futuro: sync schedulato

L'architettura non lo preclude (container cron che esegue `garmin_sync.py` ogni 6h nella cartella `fit_files/` condivisa). `garmin_sync.py` è importabile (logica in funzioni, CLI in `main()`).

## Uso

- **Tag sessione:** in dettaglio sessione puoi marcare **Easy run** (disattiva i KPI per-lap, esclude dalle viste ripetute) e **Track session** (arrotondamento distanze stretto). Puoi inserire un **target teorico** per popolare SPS-T e i delta per-lap. Ogni modifica ricalcola subito i KPI.
- **Home:** carico della settimana, trend HR @ ritmo di riferimento (il tracker del progresso aerobico), trend EF per tipo di sessione, trend SPS, indicatore ACWR e la suite completa di alert (volume, ACWR, easy ratio, SPS, pace fade, HRR60, decoupling).
- **Compare:** scegli una sessione di riferimento; le sessioni con la stessa etichetta (o time-based entro ±1 min) vengono auto-selezionate. Grafici sovrapposti per ripetuta (ritmo, HR, EA, Lap Score) e tabella delta con frecce.
- **Weekly:** totali Lun–Dom, volume giornaliero, TRIMP, ACWR, alert se l'aumento settimanale supera il +10%.

## Chat AI (assistente di analisi)

Ogni schermata di **dettaglio sessione** ha in fondo un pannello **AI coach**: puoi fare domande in linguaggio naturale sulla sessione (es. *"Quanto è stato regolare il mio ritmo?"*, *"Come si confronta con le ultime ripetute uguali?"*). Funziona così, passo per passo:

**Passo 1 — ottieni una chiave API Anthropic.** Crea un account su [console.anthropic.com](https://console.anthropic.com), genera una API key (inizia con `sk-ant-...`).

**Passo 2 — inseriscila nel file `.env`** e riavvia `python app.py`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

**Passo 3 — usa la chat.** Apri una sessione → pannello **AI coach** in fondo → scrivi e premi Invio (o **Send**). La risposta arriva **in streaming, token per token**. Il pulsante **Clear** azzera la conversazione (la cronologia è tenuta lato browser, per sessione, e inviata a ogni domanda per dare contesto).

**Cosa viene inviato all'AI.** Per rispondere con numeri reali, il server costruisce il contesto con: metadati e flag della sessione, tutti i KPI, la tabella per-lap compatta, il tuo profilo da `config.py` (zone HR, ritmi target) e le ultime 5 sessioni con la stessa etichetta. Sotto al pannello è mostrato l'avviso: *"Your session data is sent to the AI provider to generate this response."*

**Privacy e sicurezza.** La chiave vive **solo lato server** (`.env`), non viene mai esposta nel browser: il browser parla solo con il tuo Flask, che a sua volta chiama Anthropic. Senza chiave configurata la chat mostra esattamente *"AI not configured — add ANTHROPIC_API_KEY to .env"* e non viene inviato nulla.

> Modello usato: `claude-sonnet-4-6` (impostato in `app.py`, costante `CHAT_MODEL`).

## Struttura

```
app.py            Backend Flask — parsing FIT, motore KPI, API REST, proxy chat AI
config.py         Profilo utente, soglie KPI, pesi SPS
static/index.html Frontend completo (HTML + CSS + JS in un unico file)
garmin_sync.py    Download automatico FIT da Garmin Connect
sessions.json     Store sessioni (creato al primo import)
fit_files/        Cartella di drop dei FIT (target dello Scan)
```
