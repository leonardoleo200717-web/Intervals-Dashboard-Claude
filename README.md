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

Usa la libreria non ufficiale `garminconnect`. Credenziali in `.env`:

```bash
python garmin_sync.py                          # ultimi 30 giorni
python garmin_sync.py --days 60
python garmin_sync.py --from 2026-04-01 --to 2026-05-31
python garmin_sync.py --limit 10               # ultime N attività
python garmin_sync.py --all-types              # include attività non di corsa
python garmin_sync.py --output altra_cartella
```

I file vengono salvati in `fit_files/`; poi premi **Scan fit_files/** nella dashboard per importarli. Al primo login con 2FA la libreria chiede il codice OTP; la sessione viene memorizzata in `.garmin_session` per gli accessi successivi.

> Nota: è un'API non ufficiale e può smettere di funzionare se Garmin cambia gli endpoint. In quel caso usa l'esportazione manuale.

### 3. Futuro: sync schedulato

L'architettura non lo preclude (container cron che esegue `garmin_sync.py` ogni 6h nella cartella `fit_files/` condivisa). `garmin_sync.py` è importabile (logica in funzioni, CLI in `main()`).

## Uso

- **Tag sessione:** in dettaglio sessione puoi marcare **Easy run** (disattiva i KPI per-lap, esclude dalle viste ripetute) e **Track session** (arrotondamento distanze stretto). Puoi inserire un **target teorico** per popolare SPS-T e i delta per-lap. Ogni modifica ricalcola subito i KPI.
- **Home:** carico della settimana, trend HR @ ritmo di riferimento (il tracker del progresso aerobico), trend EF per tipo di sessione, trend SPS, indicatore ACWR e la suite completa di alert (volume, ACWR, easy ratio, SPS, pace fade, HRR60, decoupling).
- **Compare:** scegli una sessione di riferimento; le sessioni con la stessa etichetta (o time-based entro ±1 min) vengono auto-selezionate. Grafici sovrapposti per ripetuta (ritmo, HR, EA, Lap Score) e tabella delta con frecce.
- **Weekly:** totali Lun–Dom, volume giornaliero, TRIMP, ACWR, alert se l'aumento settimanale supera il +10%.
- **Chat AI:** le risposte arrivano in streaming token-per-token (SSE).

## Struttura

```
app.py            Backend Flask — parsing FIT, motore KPI, API REST, proxy chat AI
config.py         Profilo utente, soglie KPI, pesi SPS
static/index.html Frontend completo (HTML + CSS + JS in un unico file)
garmin_sync.py    Download automatico FIT da Garmin Connect
sessions.json     Store sessioni (creato al primo import)
fit_files/        Cartella di drop dei FIT (target dello Scan)
```
