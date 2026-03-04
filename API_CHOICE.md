- Étudiant : Dylan Gay 
- API choisie : Open-Meteo
- URL base : https://api.open-meteo.com/
- Documentation officielle / README :
- Auth : None
- Endpoints testés : /v1/forecast?latitude=48.85&longitude=2.35&current_weather=true
- Hypothèses de contrat (champs attendus, types, codes) : HTTP 200, JSON, champ current_weather, temperature float, windspeed float
- Limites / rate limiting connu : rate limit raisonnable, timeout 3s
- Risques (instabilité, downtime, CORS, etc.) : API Down, latence variable
Note: Automatic scheduled execution could not be configured because the PythonAnywhere free plan does not allow scheduled tasks.
The endpoint /run can still be triggered manually or by an external scheduler (cron, GitHub Actions, etc.).
