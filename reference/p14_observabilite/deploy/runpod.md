# Déploiement cloud de la démo (RunPod)

Architecture : l'API FastAPI (pod CPU) appelle vLLM (pod GPU) en HTTPS avec une clé.

```
Client ── HTTPS ──► API FastAPI (pod CPU, image GHCR)  ── HTTPS + clé ──► vLLM (pod GPU)
                     /health /docs /triage                                SteGONZALEZ/qwen3-triage-chsa
```

Aucun secret dans ce fichier ni dans Git : tokens et clés vivent dans RunPod (Secrets) et dans `.env` (ignoré).

## A. Préparation (gratuite, aucun pod allumé)

1. Poids : dépôt Hugging Face privé `SteGONZALEZ/qwen3-triage-chsa` (Qwen3-1.7B-Base : SFT checkpoint-550 + DPO checkpoint-150, jetons spéciaux entraînés, fusionné et validé au test gelé, 453/500, sous-triage 5,4 % ; la version instruct précédente reste dans l'historique du dépôt).
2. Token Hugging Face **Read** `runpod-read` → RunPod › Settings › Secrets, nom `HF_TOKEN`.
3. Clé vLLM (40 caractères, sans accent) → RunPod › Secrets, nom `VLLM_API_KEY`, et dans `.env` local (`VLLM_API_KEY=...`).
   ```powershell
   -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | % {[char]$_})
   ```
4. Image de l'API : CI au vert sur `master` → la CD publie `ghcr.io/gonzalezcourrier34-glitch/medical_triage_agent/api:sha-<commit>`.
   GitHub › Packages › `api` › Package settings › visibilité **Public**.
5. Templates RunPod (My Templates › New Template, gratuit) :

   **triage-vllm** (pod GPU)
   - Image : `vllm/vllm-openai:v0.31.0` (version des mesures du 12 octobre)
   - Container disk : 30 Go · aucun volume · HTTP port `8000`
   - Env : `HF_TOKEN={{ RUNPOD_SECRET_HF_TOKEN }}` et `VLLM_API_KEY={{ RUNPOD_SECRET_VLLM_API_KEY }}`
     (vLLM lit `VLLM_API_KEY` comme `--api-key` : la clé n'apparaît pas dans la commande)
   - Start command :
     `--model SteGONZALEZ/qwen3-triage-chsa --served-model-name triage-qwen3 --dtype bfloat16 --max-model-len 2048`

   **triage-api** (pod CPU)
   - Image : `ghcr.io/gonzalezcourrier34-glitch/medical_triage_agent/api:sha-<commit>`
   - Container disk : 5 Go · HTTP port `8000` · pas de start command (uvicorn est dans l'image)
   - Env : `INFERENCE_BACKEND=vllm`, `VLLM_API_KEY={{ RUNPOD_SECRET_VLLM_API_KEY }}`,
     `VLLM_BASE_URL=https://<pod-gpu>-8000.proxy.runpod.net/v1` (à compléter au lancement)

## B. Session payante (≈ 45 min, ≈ 0,60 $)

1. Déployer **triage-vllm** sur un GPU en Europe (RTX PRO 4000 / A5000). Logs : noter la version vLLM, attendre `Application startup complete`.
2. Vérifier :
   ```powershell
   curl.exe -H "Authorization: Bearer $env:VLLM_API_KEY" https://<pod-gpu>-8000.proxy.runpod.net/v1/models
   ```
3. Test gelé (20 cas, puis 500) :
   ```powershell
   uv run python benchmarks/eval_test_gele.py --mode vllm --vllm-url https://<pod-gpu>-8000.proxy.runpod.net/v1 --limit 20
   uv run python benchmarks/eval_test_gele.py --mode vllm --vllm-url https://<pod-gpu>-8000.proxy.runpod.net/v1
   ```
4. Déployer **triage-api** (CPU) avec l'URL du pod GPU. Vérifier `https://<pod-cpu>-8000.proxy.runpod.net/health`, puis `/docs` : un cas FR, un cas EN, captures d'écran.
5. Relever : GPU, région, version vLLM, temps de démarrage, latence (moyenne, P50, P95).
6. **Terminate** les deux pods (pas Stop). Vérifier que la dépense revient à ≈ 0,004 $/h.

## C. Monitoring pendant la démo (sur le poste local)

Prometheus et Grafana tournent sur le poste, avec une API locale branchée sur le pod vLLM :
le tableau de bord suit le trafic réel de la démo sans rien installer sur RunPod.

1. Dans `.env` (clé saisie localement, jamais partagée) :
   ```
   INFERENCE_BACKEND=vllm
   VLLM_BASE_URL=https://<pod-gpu>-8000.proxy.runpod.net/v1
   VLLM_API_KEY=...
   VLLM_METRICS_SCHEME=https
   VLLM_METRICS_TARGET=<pod-gpu>-8000.proxy.runpod.net
   GRAFANA_ADMIN_PASSWORD=...
   ```
2. Docker Desktop démarré, puis :
   ```powershell
   docker compose --profile monitoring up -d api prometheus grafana
   ```
3. API : http://127.0.0.1:8000/docs · Grafana : http://127.0.0.1:3000 (dashboard « Operations Overview »)
   · Prometheus : http://127.0.0.1:9090 › Status › Targets (API et vLLM « UP »).
4. Générer du trafic :
   ```powershell
   uv run python benchmarks/demo_cas_inconnus.py --mode api --api-url http://127.0.0.1:8000
   ```
   Les courbes apparaissent après une à deux minutes (relevé toutes les 15 s, débits sur 5 min).
5. Après la démo : `docker compose --profile monitoring down`, puis Terminate du pod.

## Limites connues

- L'API de démo n'a pas d'authentification : URL à ne pas diffuser, pods terminés après la démo.
- Le déploiement sur RunPod est manuel (changement du tag d'image) ; la CD automatise build et publication.
- Les données de démo doivent rester fictives : RunPod n'est pas un hébergeur HDS.
