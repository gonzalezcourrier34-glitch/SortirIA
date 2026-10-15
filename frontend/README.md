# Interface SortirIA (Angular)

Reprise de l'architecture de Chess Agent (projet 13) : Angular 21 en
composants autonomes, proxy `/api` vers l'API FastAPI, recherche diffusée en
Server-Sent Events. Voir `docs/interface.md`.

```bash
npm install
npm start          # http://localhost:4210 (l'API doit tourner sur http://localhost:8010)
npm test           # tests (vitest)
```

| Fichier | Rôle |
|---|---|
| `src/app/app.component.*` | l'écran : filtres, question, réponse, cartes, détail, partenaires, fenêtre des étapes |
| `src/app/search.service.ts` | appels à l'API `/api/v1` et lecture du flux de recherche |
| `src/app/sse.ts` | découpage et lecture des messages Server-Sent Events |
| `src/app/search.model.ts` | types échangés avec l'API |
| `proxy.conf.json` / `proxy.docker.json` | adresse de l'API en local / dans docker compose |
