# Interval Training Dashboard

Dashboard locale per analizzare gli allenamenti di corsa con ripetute a partire dai file FIT di Garmin. Tutto gira sul tuo PC: nessun cloud, nessun database. Calcola KPI avanzati (EF, Pace Fade, HRR60, TRIMP, ACWR, predizione maratona…) e li presenta in un'interfaccia interattiva per analisi della sessione, confronto storico, monitoraggio del carico settimanale e proiezione del tempo di gara.

---

## Indice

1. [Requisiti e installazione](#requisiti-e-installazione)
2. [Configurazione (.env)](#configurazione-env)
3. [Guida rapida (5 minuti)](#guida-rapida-5-minuti)
4. [Le viste della dashboard](#le-viste-della-dashboard)
5. [Flussi d'uso tipici](#flussi-duso-tipici)
6. [Manuale dei KPI](#manuale-dei-kpi)
   - [Come leggere colori e soglie](#come-leggere-colori-e-soglie)
   - [KPI di sessione](#kpi-di-sessione)
   - [KPI per-lap](#kpi-per-lap)
   - [Le funzioni di punteggio](#le-funzioni-di-punteggio-score)
   - [KPI settimanali](#kpi-settimanali-wk-01wk-10)
   - [Il predittore maratona](#il-predittore-maratona)
   - [Tabella soglie](#tabella-soglie-configpy)
7. [Come ottenere i file FIT da Garmin](#come-ottenere-i-file-fit-da-garmin)
8. [Chat AI (assistente di analisi)](#chat-ai-assistente-di-analisi)
9. [Struttura del progetto](#struttura-del-progetto)

---

## Requisiti e installazione

- Python 3.11+

```bash
pip install -r requirements.txt
# oppure: pip install flask fit-tool garminconnect python-dotenv anthropic
```

Avvio:

```bash
python app.py
```

Apri il browser su **http://localhost:5000**. Alla prima apertura la dashboard è vuota: importa qualche sessione (vedi [Guida rapida](#guida-rapida-5-minuti)).

## Configurazione (.env)

Copia il template e inserisci i tuoi valori (tutti opzionali per il funzionamento base):

```bash
cp .env.example .env
```

- `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` — abilitano la chat AI. Configurane almeno una (vedi [Chat AI](#chat-ai-assistente-di-analisi)). Restano **solo lato server**, mai nel browser.
- `GARMIN_EMAIL` / `GARMIN_PASSWORD` — servono solo per `garmin_sync.py`.

Tutti i parametri di calcolo (zone HR, soglie KPI, pesi dei punteggi, obiettivo maratona) vivono in **`config.py`** — niente è hard-coded altrove. Modificali lì e riavvia.

---

## Guida rapida (5 minuti)

1. **Avvia** `python app.py` e apri `http://localhost:5000`.
2. **Importa una sessione.** Clicca **⬆ Import FIT** nella barra laterale e trascina uno o più `.fit`, oppure copiali in `fit_files/` e premi **🔄 Scan fit_files/**. Un toast riepiloga aggiunti / duplicati / saltati. (Come scaricare i FIT: [sezione Garmin](#come-ottenere-i-file-fit-da-garmin).)
3. **Apri la sessione** dalla Home o da History. Controlla che etichetta (es. `10×400 m`), numero di ripetute, warm-up/cool-down e ritmi corrispondano a Garmin Connect.
4. **Sistema i tag se serve.** Marca **Easy run** per un fondo lento, **Track session** per la pista. Se il rilevamento ripetute non è corretto, digita la struttura nella casella **Override** (es. `5x1200m`) o correggi i singoli lap col menu **Type**.
5. **Esplora:** trend aerobico in Home, confronto in Compare, carico in Weekly, proiezione di gara in Marathon.

> Principio guida: **nessun dato finto**. Quando un valore non è calcolabile (manca l'HR, manca la traccia secondo-per-secondo, dati insufficienti) la dashboard mostra `—` con il motivo, mai un numero inventato.

---

## Le viste della dashboard

La navigazione è nella barra laterale a sinistra.

### 🏠 Home
Sintesi della settimana corrente e dei trend di lungo periodo:
- **Card della settimana:** km totali (vs obiettivo), numero sessioni, Weekly EF, easy ratio, TRIMP, ACWR.
- **Banner di alert:** volume, ACWR, easy ratio, SPS, pace fade, HRR60, decoupling (compaiono solo se una soglia è superata).
- **HR @ ritmo di riferimento** — il grafico headline del progresso aerobico (HR media nella banda 3:55–4:05/km; in discesa = stai migliorando).
- **Trend EF** filtrato per tipo di sessione, **trend SPS**, **indicatore ACWR** con banda sweet-spot 0.8–1.3 ombreggiata.
- **Race predictor** (Riegel) dal miglior sforzo lungo, e le ultime 3 sessioni a ripetute.

### 📄 Dettaglio sessione
- Intestazione con etichetta, data, badge (Track/Easy/Distance/Time) e SPS-T / SPS-I.
- **Checkbox** Easy / Track e casella **target teorico** (ricalcolo immediato a ogni modifica).
- **Riquadro "Detected set":** struttura rilevata, fonte usata (badge) e casella **Override** per forzarla.
- **Card KPI**, **grafico HR nel tempo**, pannello decoupling, sparkline dei Lap Score.
- **Tabella per-lap:** righe attive bianche, recuperi grigi, WU/CD in corsivo; menu **Type** per correggere ogni lap; export **CSV**.
- **Pannello AI coach** in fondo.

### 📋 History
Lista di tutte le sessioni (più recenti in alto), filtrabile per tipo (interval/easy/track) e tipo di ripetuta (distance/time). Clic su una riga → dettaglio.

### 🔬 Compare
Scegli una sessione di riferimento; vengono proposte le sessioni **simili — non identiche** (stesso tipo, time vs distance, ordinate per vicinanza di dimensione e numero ripetute), così `3×10'` si confronta con `4×8'` o `5×2'` e anche le sessioni miste trovano corrispondenze. Grafici sovrapposti ripetuta-per-ripetuta (ritmo, HR, EA, Lap Score) allineati per indice (R1, R2…) e tabella delta con frecce. Se confronti corse continue (senza ripetute), i grafici per-rep vengono nascosti e resta il confronto a livello di sessione; per avere i grafici per-rep dichiara la struttura nella casella Override.

### 📅 Weekly
Report settimanale Lun–Dom: card WK-01…WK-10, grafico volume giornaliero (interval vs easy), volume rolling 4 settimane, lista sessioni del periodo e banner di alert quando l'aumento settimanale supera il +10%.

### 🎯 Marathon
Proiezione del tempo di maratona con un ensemble di 4 modelli (vedi [Il predittore maratona](#il-predittore-maratona)): stato Ahead / On track / Behind, grafico previsione per settimana vs obiettivo, tabella "What changed" e form **Goal & inputs** per impostare obiettivo e gara di riferimento.

### 💬 AI Coach
Chat che ragiona **su tutte le sessioni**, non su una sola: chiedi *"Sto migliorando?"*, *"Com'è il mio carico e il rischio infortuni?"*, *"Sono in linea per la maratona?"*. Il contesto inviato all'AI riassume i totali settimanali, il trend HR @ ritmo di riferimento e EF per tipo di allenamento, le ultime sessioni e la proiezione maratona, così le risposte sono basate sui tuoi numeri reali. Ci sono domande suggerite con un clic, un selettore del modello e il pulsante Clear. Richiede una chiave AI in `.env` come la chat di sessione.

---

## Flussi d'uso tipici

### Tag della sessione (Easy / Track)
Due flag per sessione, modificabili in qualsiasi momento; ogni cambio ricalcola subito i KPI lato server.

| Flag | Effetto |
|---|---|
| (nessuno) | Rilevamento ripetute attivo, tutti i KPI per-lap, inclusa nelle viste a ripetute |
| **Easy run** | Niente rilevamento ripetute né KPI per-lap; esclusa dai confronti; conta nei totali settimanali; usa EF + decoupling + HR@RefPace |
| **Track session** | Come una sessione a ripetute ma con arrotondamento distanze "stretto" (range pista) |

### Target teorico
Inserendo un ritmo (o durata) obiettivo per ripetuta popoli **SPS-T** e le colonne **Δ vs target** della tabella per-lap. Senza target, la dashboard usa il **target inferito** (mediana del ritmo delle ripetute) per SPS-I e Δ inf.

### Rilevamento ripetute e correzione
La struttura viene riconosciuta, **in ordine di affidabilità**, da:
1. **Correzione manuale per-lap** (menu Type nella tabella) — vince sempre.
2. **Struttura digitata** da te nel riquadro Override (es. `5x5'`, `10x90"`, `5x4km p1'`).
3. **Marker di intensità del Garmin** registrati in ogni lap (warm-up / active / rest / cool-down): se hai seguito un allenamento strutturato sull'orologio, ogni lap è già etichettato alla fonte — drill, warm-up e cool-down esclusi senza indovinare dal ritmo (badge *"from Garmin lap markers"*).
4. **Convenzione di denominazione** nel titolo dell'attività.
5. **Euristica sul ritmo** come ultima spiaggia.

Warm-up e cool-down sono **sempre** esclusi. Formati accettati nell'Override: minuti `5'`, secondi `90"`, mm:ss `1:30`, km `4km`, metri `400m`, con recupero opzionale `p1'`/`r400m`/`rec200m`.

### Autolap spezzato
Se avevi l'autolap attivo (es. lap automatico ogni 1000 m) **e** premevi il pulsante a fine ripetuta, una singola ripetuta finisce registrata come due lap (1000 m + 200 m). Quando **dichiari il target** (titolo o Override, es. `5x1200m`) la dashboard li **ricongiunge** nell'unica ripetuta da 1200 m — durata sommata, HR pesata sul tempo — e la marca con ⛓ in tabella. Funziona solo col target dichiarato; non tocca le sessioni senza struttura e non unisce mai i drill.

### Recovery adherence
Se indichi il recupero pianificato nel titolo/Override (es. `p1'` o `r400m`), la dashboard confronta i recuperi reali con quelli previsti e mostra una card di aderenza (verde ≤110%, ambra ≤140%, rosso oltre).

---

## Manuale dei KPI

Tutte le formule e le soglie vivono in `config.py`. Unità metriche ovunque (km, mm:ss/km, bpm).

### Come leggere colori e soglie
- **Verde** = nel target · **Ambra** = attenzione · **Rosso** = alert. Le soglie sono in `THRESHOLDS` (vedi [tabella](#tabella-soglie-configpy)).
- `—` significa "non calcolabile" (dato mancante), **non** zero.
- I KPI cambiano in base ai tag: le sessioni *easy* mostrano EF + decoupling + HR@RefPace; le sessioni a *ripetute* mostrano EF, Pace Fade, Pace CV, HRR60, Cardiac Cost, SPS.

### KPI di sessione

| ID | Nome | Formula | Target | Come leggerlo |
|---|---|---|---|---|
| **KPI-01** | Efficiency Factor (EF) | `velocità (m/min) / HR media` | trend in salita | Indice di Friel. **Monotòno: più alto = meglio** (più veloce a parità di HR, o stesso ritmo a HR più bassa). |
| **KPI-02** | Aerobic Decoupling | `(EF_1ªmetà − EF_2ªmetà) / EF_1ªmetà × 100` | < 5% | **Solo sessioni steady** (fondi, tempo continui). Quanto "deriva" l'HR a ritmo costante. Sulle ripetute è soppresso (la deriva è voluta). |
| **KPI-03** | SPS-T | `Σ pesi × punteggi_k` con target teorico | > 75 | Session Performance Score sul **tuo** target. Null finché non imposti il target. |
| **KPI-04** | SPS-I | come SPS-T ma con target inferito (mediana ripetute) | > 75 | Sempre calcolabile; voto di esecuzione "auto-tarato". |
| **KPI-05** | Pace Fade | `(ritmo_ultima − ritmo_prima) / ritmo_prima × 100` | < +2% | **Solo ripetute.** Positivo = stai rallentando rep dopo rep. Segnale primario di esecuzione. |
| **KPI-06** | Pace Consistency (CV) | `dev.std(ritmi rep) / media(ritmi rep) × 100` | < 2% | **Solo ripetute.** Basso = passo uniforme tra le ripetute. |
| **KPI-07** | HR @ Reference Pace | HR media dei tratti nella banda di riferimento (3:55–4:05/km) | trend in discesa | Il **tracker del progresso aerobico**: stessa velocità a meno battiti = adattamento. Calcolato sulla traccia secondo-per-secondo (serve ≥ 3 min in banda). |

Pesi di SPS (`SPS_WEIGHTS`): ritmo 0.40, EF 0.30, fade/decoupling 0.20, recupero 0.10. Sulle ripetute lo slot "decoupling" è riempito dal punteggio di Pace Fade.

### KPI per-lap
Calcolati sui lap **attivi**; i recuperi mostrano solo distanza, durata, HR e HRR60/RQS.

| ID | Nome | Formula |
|---|---|---|
| **LAP-01** | Pace | `durata / (distanza/1000)` |
| **LAP-02** | Avg HR | dal FIT |
| **LAP-03** | EA per lap | `ritmo / HR media` |
| **LAP-04** | HRR60 (Heart-Rate Recovery) | `HR a fine ripetuta − HR 60 s dopo`, dalla traccia secondo-per-secondo · > 25 bpm buono, 15–25 ok, < 15 flag |
| **LAP-04b** | RQS (fallback) | `HR lap di recupero / HR lap attivo precedente × 100` — usato **solo** quando manca la traccia secondo-per-secondo |
| **LAP-05** | Cardiac Cost | `HR attiva − HR del recupero precedente` |
| **LAP-06** | Δ vs target teorico | `(ritmo − target)/target × 100` |
| **LAP-07** | Δ vs target inferito | come sopra ma vs mediana di sessione |
| **LAP-08** | Lap Score | `Σ pesi × punteggi` (ritmo, EA vs media sessione, RQS del recupero seguente) |

> **Perché HRR60 e non la media dell'HR di recupero?** HRR60 è un marcatore validato di riattivazione parasimpatica, molto più robusto della media dell'HR di recupero (che riflette soprattutto la durata del recupero, non la capacità di recuperare).

### Le funzioni di punteggio (score)
Funzioni lineari a tratti, condivise da SPS e Lap Score:
- **Ritmo:** Δ 0% → 100 · Δ +3% (più lento) → 70 · Δ −3% (più veloce) → 85 · |Δ| ≥ 10% → 0.
- **EA:** normalizzato vs la mediana EA delle ultime 8 sessioni **con la stessa etichetta**. ±5% → 75 · +10% → 100 · −10% → 40. Con < 3 sessioni storiche → 75 (neutro).
- **Decoupling:** < 3% → 100 · 3–5% → 80 · 5–8% → 60 · > 8% → 20.
- **Recupero:** da HRR60 se disponibile (> 30 → 100 · 25–30 → 85 · 15–25 → 60 · < 15 → 30); fallback su RQS quando manca la traccia.
- **Componenti mancanti:** i pesi rimanenti vengono rinormalizzati (es. senza HR si scartano EA/RQS e si riscalano ritmo+decoupling a somma 1).

### KPI settimanali (WK-01…WK-10)
Settimane ISO Lun–Dom.

| ID | Nome | Cosa misura | Target |
|---|---|---|---|
| **WK-01** | Km totali | somma settimanale | barra vs `weekly_km_target` |
| **WK-02** | Numero sessioni | conteggio per tipo | — |
| **WK-03** | HR media | media pesata sulla durata | stabile/in calo |
| **WK-04** | Weekly EF (solo easy) | EF medio pesato sulla distanza dei fondi | in salita su 4–8 settimane |
| **WK-05** | Volume di qualità | Σ distanza dei lap attivi | — |
| **WK-06** | SPS settimanale | media SPS delle sessioni a ripetute | > 70 |
| **WK-07** | Δ km settimana-su-settimana | `(questa − precedente)/precedente × 100` | < +10% (oltre → alert) |
| **WK-08** | Easy ratio | tempo sotto la soglia zona-2 / tempo totale (dalle tracce HR) | ≥ 75% (check 80/20) |
| **WK-09** | TRIMP settimanale | TRIMP di Edwards: Σ `minuti_in_zona × indice_zona` (zone 1–5) | carico pesato per intensità |
| **WK-10** | ACWR | `TRIMP_7gg / media(TRIMP_28gg)` | 0.8–1.3 sweet spot · > 1.5 → rosso |

ACWR (acute:chronic workload ratio) è il monitor del rischio infortuni: molto più informativo della sola % di km, specie con storia di infortuni.

### Il predittore maratona
La scheda **Marathon** stima il tempo con 4 modelli, ognuno con il suo bias dichiarato:

| Modello | Tipo | Cosa fa |
|---|---|---|
| **Riegel** | race-based | `T2 = T1 · (D2/D1)^1.06`. Soffitto ottimistico (spesso 10+ min veloce sulla maratona). |
| **Daniels VDOT** | race-based | Calcola il VDOT dalla gara di riferimento e lo inverte sulla maratona (ricerca binaria). |
| **Tanda 2011** | training-based | `Pm = 17.1 + 140·e^(−0.0053·K) + 0.55·P` con K = km medi/settimana e P = ritmo medio di allenamento (8 settimane). È il **motore settimanale** e il "pavimento di resistenza" — mostrato a parte (sottostima i target sub-2:47). |
| **Vickers–Vertosick 2016** | race + km | Correzione maratona da gara + chilometraggio. **Disattivato** finché non si caricano i coefficienti pubblicati (vedi nota sotto). |

- **Stima centrale:** media pesata (`MARATHON.ensemble_weights`) dei modelli race-equivalent disponibili; Tanda resta separato come floor.
- **Stato:** `centrale − obiettivo`, classificato **Ahead / On track (±1%) / Behind** con delta, banda di incertezza ±3% e spread min–max.
- **Grafico per settimana:** Tanda (si muove con volume e ritmo) + centrale race-equivalent vs la linea obiettivo; sotto, "What changed" con il Δ settimanale di K, P e previsione.
- **Goal & inputs:** imposti obiettivo (nome/data/ritmo) e una gara di riferimento ben corsa di cui i modelli si fidano. Le impostazioni sono salvate in `settings.json`.

> **Nota onesta su Vickers–Vertosick (modello D):** i coefficienti pubblicati non sono stati verificabili da una fonte primaria in questo ambiente, quindi il modello è mostrato in stato *"coefficients not loaded"* invece di girare su numeri inventati. Per attivarlo, inserisci i coefficienti reali in `config.VICKERS_COEFFS` e completa `_vickers_marathon_time` in `app.py`.

### Tabella soglie (config.py)
Valori di default (calibrati per l'utente reale; modificabili in `config.py`):

| Parametro | Valore | Significato |
|---|---|---|
| `hr_max` | 190 bpm | usato per le zone HR / TRIMP |
| `zone2_hr` | 125–143 | tetto del fondo facile (easy ratio) |
| `threshold_hr` | 177 | HR di soglia anaerobica |
| `marathon_target_pace` | 232 s/km (3:52) | obiettivo maratona (2:43) |
| `REFERENCE_PACE_BAND` | 235–245 s/km | banda per HR@RefPace (3:55–4:05/km) |
| `decoupling_good / alert` | 5% / 8% | decoupling buono / alert |
| `pace_fade_alert` | 2% | alert fade ripetute |
| `pace_cv_alert` | 2% | alert consistenza ripetute |
| `hrr60_good / alert` | 25 / 15 bpm | recupero buono / flag |
| `sps_alert` | 50 | sotto = rosso |
| `weekly_increase_alert` | 10% | alert aumento km settimanale |
| `easy_ratio_target` | 75% | obiettivo easy ratio |
| `acwr_low / high / alert` | 0.8 / 1.3 / 1.5 | sweet spot e zona rossa ACWR |

---

## Come ottenere i file FIT da Garmin

### 1. Esportazione manuale (sempre funziona, nessuna configurazione)

1. [connect.garmin.com](https://connect.garmin.com) → **Attività** → apri un'attività
2. Icona ingranaggio (⚙) in alto a destra → **Esporta originale**
3. Estrai lo `.zip` scaricato → all'interno trovi il file `.fit`
4. Trascinalo nella dashboard (**Import FIT**) oppure copialo in `fit_files/` e premi **Scan fit_files/**

**Esportazione completa dello storico:** impostazioni account Garmin → **Gestione dati → Esporta i tuoi dati**. Garmin invia via email un link a uno ZIP con la cartella `Activities/` contenente tutti i FIT registrati. Estrai tutto in `fit_files/` e premi **Scan**. È il modo consigliato per importare mesi di storico in una volta.

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

Lo script salta i file già presenti (riconosce l'ID attività nel nome), gestisce il limite di richieste di Garmin (attesa di 60 s e un nuovo tentativo) e a fine esecuzione stampa un riepilogo: scaricati / saltati / falliti. Salva anche un file `.meta.json` accanto a ogni FIT con il **titolo dell'attività**, così la convenzione di denominazione (es. `5x4km p1'`) guida il rilevamento ripetute.

**Passo 4 — importa nella dashboard.** I file finiscono in `fit_files/`. Apri la dashboard e premi **Scan fit_files/** nella barra laterale: un toast mostra quanti file sono stati aggiunti, saltati (duplicati o non-corsa) o in errore.

> **Risoluzione problemi.** È un'API non ufficiale e può cambiare. Se il login fallisce di continuo: verifica email/password in `.env`; cancella la cartella `.garmin_session/` per forzare un nuovo login; controlla che il tuo account non sia bloccato su [sso.garmin.com](https://sso.garmin.com). Se nulla funziona, usa sempre l'**esportazione manuale** (sezione 1) come ripiego.

### 3. Futuro: sync schedulato

L'architettura non lo preclude (container cron che esegue `garmin_sync.py` ogni 6h nella cartella `fit_files/` condivisa). `garmin_sync.py` è importabile (logica in funzioni, CLI in `main()`).

---

## Chat AI (assistente di analisi)

Ogni schermata di **dettaglio sessione** ha in fondo un pannello **AI coach**: puoi fare domande in linguaggio naturale sulla sessione (es. *"Quanto è stato regolare il mio ritmo?"*, *"Come si confronta con le ultime ripetute uguali?"*). Funziona così, passo per passo:

**Passo 1 — ottieni una o più chiavi API.** Puoi scegliere tra più provider e decidere di volta in volta quale modello interrogare. Configurane almeno uno:
- **Anthropic (Claude):** [console.anthropic.com](https://console.anthropic.com) → API key (`sk-ant-...`)
- **DeepSeek:** [platform.deepseek.com](https://platform.deepseek.com) → API key
- **OpenAI:** [platform.openai.com](https://platform.openai.com) → API key

**Passo 2 — inserisci le chiavi nel file `.env`** (solo quelle che hai) e riavvia `python app.py`:

```
ANTHROPIC_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=...
OPENAI_API_KEY=...
```

**Passo 3 — usa la chat.** Apri una sessione → pannello **AI coach** in fondo. In alto c'è un menu **Model**: elenca solo i provider/modelli per cui hai messo una chiave, raggruppati per provider (Claude, DeepSeek, OpenAI). Scegli il modello, scrivi e premi Invio (o **Send**). La risposta arriva **in streaming, token per token**. Il pulsante **Clear** azzera la conversazione (la cronologia è tenuta lato browser, per sessione, e inviata a ogni domanda per dare contesto). Comodo quando un provider è sovraccarico (`overloaded`): cambi modello e prosegui.

**Cosa viene inviato all'AI.** Per rispondere con numeri reali, il server costruisce il contesto con: metadati e flag della sessione, tutti i KPI, la tabella per-lap compatta, il tuo profilo da `config.py` (zone HR, ritmi target) e le ultime 5 sessioni con la stessa etichetta. Sotto al pannello è mostrato l'avviso: *"Your session data is sent to the selected AI provider to generate this response."*

**Privacy e sicurezza.** Le chiavi vivono **solo lato server** (`.env`), non vengono mai esposte nel browser: il browser parla solo con il tuo Flask, che a sua volta chiama il provider scelto. Senza nessuna chiave configurata la chat mostra il messaggio *"AI not configured"* e non viene inviato nulla.

> Provider e modelli sono definiti in `config.py` (`AI_PROVIDERS`); il default è Anthropic `claude-sonnet-4-6`. I provider stile OpenAI (DeepSeek, OpenAI e qualsiasi endpoint compatibile) usano lo stesso percorso di codice — basta aggiungerli al registro.

---

## Struttura del progetto

```
app.py            Backend Flask — parsing FIT, motore KPI, predittore, API REST, proxy chat AI
config.py         Profilo utente, soglie KPI, pesi SPS, provider AI, config maratona
static/index.html Frontend completo (HTML + CSS + JS in un unico file)
garmin_sync.py    Download automatico FIT da Garmin Connect
test_suite.py     Suite di test (parsing, KPI, rilevamento, predittore, API)
sessions.json     Store sessioni (creato al primo import, git-ignored)
settings.json     Impostazioni app: obiettivo maratona, gara di riferimento (git-ignored)
fit_files/        Cartella di drop dei FIT (target dello Scan)
```

### API REST (riferimento rapido)

| Metodo | Percorso | Scopo |
|---|---|---|
| GET | `/api/sessions` · `/api/sessions/:id` | elenco / dettaglio sessioni |
| PATCH | `/api/sessions/:id` | aggiorna flag, target, struttura, tipi lap → ricalcola |
| POST | `/api/upload` · `/api/scan` | importa FIT (upload o scan di `fit_files/`) |
| GET | `/api/weekly` | aggregato ultime 8 settimane ISO |
| GET | `/api/predictions` · `/api/marathon` | race predictor · predittore maratona |
| GET/PATCH | `/api/marathon/settings` | obiettivo e gara di riferimento |
| POST | `/api/chat` · `/api/chat/stream` | chat AI (proxy, streaming SSE) |
| GET | `/api/sessions/:id/export.csv` | export tabella per-lap |
| GET | `/api/config` | slice di `config.py` per il frontend |

### Test

```bash
python test_suite.py
```

Copre parsing FIT reale (CRC/UTF-8), parsing struttura, rilevamento ripetute (spec / euristica / marker Garmin / autolap), motore KPI, settimanali + ACWR + TRIMP, predittore maratona e tutti gli endpoint API.
