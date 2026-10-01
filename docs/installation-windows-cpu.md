# Installer le projet sur un PC Windows (sans carte graphique NVIDIA)

Ce guide te fait passer, étape par étape, d'un PC Windows vierge à un assistant qui fonctionne.
Suis les étapes dans l'ordre et coche-les au fur et à mesure.

## Comment lire ce guide

- Chaque commande est précédée de l'endroit où la taper : **📍 Où**.
- **PowerShell** = le terminal de Windows. Pour l'ouvrir : menu Démarrer, tape « Terminal » ou « PowerShell », puis Entrée.
- Pour te placer dans un dossier avant une commande, on utilise `cd` (« change directory »). Exemple : `cd C:\dev\assistant-regles-w40k`.
- Tu n'as **pas besoin d'ouvrir de terminal Linux (WSL)**. Docker Desktop s'en sert en coulisses, tout seul.
- Remplace `<toi>` par ton nom d'utilisateur Windows.

## Vue d'ensemble

| # | Étape | Où |
|---|---|---|
| 0 | Préparer les fichiers à transférer | Ton PC actuel |
| 1 | Vérifier que le PC est prêt | PC Windows |
| 2 | Installer les outils | PC Windows |
| 3 | Récupérer le code | PC Windows |
| 4 | Installer Python et les bibliothèques | PC Windows |
| 5 | Configurer le fichier `.env` | PC Windows |
| 6 | Démarrer la base de données | PC Windows |
| 7 | Copier les données | PC Windows |
| 8 | Indexer (premier lancement) | PC Windows |
| 9 | Vérifier que tout marche | PC Windows |
| 10 | Lancer les tests | PC Windows |

---

## Étape 0 : Préparer les fichiers (sur ton PC actuel)

- [ ] **Vérifie que ton travail est bien poussé sur GitHub.** Le PC Windows ne recevra que ce qui est sur GitHub.

  📍 **Où :** terminal de ton PC actuel, dans le dossier du projet
  ```bash
  git status
  git log origin/main..HEAD
  ```
  La première commande doit dire qu'il n'y a rien à valider. La seconde ne doit rien afficher.

- [ ] **Rassemble les fichiers à transférer** (clé USB ou cloud privé, jamais sur GitHub) :
  - `data/interim/chunks/chunks.jsonl` : **obligatoire**
  - `data/core-rules/gold_set_w40k_v11.xlsx` : optionnel (pour l'évaluation). Le notebook 08 lit `data/core-rules/gold_set.xlsx` : renomme le fichier ou adapte le chemin dans le notebook
  - le PDF du livre de règles : optionnel (inutile pour utiliser l'assistant)
  - ton fichier `.env` : optionnel (voir la version courte de l'étape 5). Il contient ta clé API : supprime-le de la clé USB ou du cloud une fois copié

- [ ] **Note une question de test** et les 3 premiers résultats que renvoie `chercher-regles` (les codes du type `03.01`). Tu les compareras à l'étape 9. Choisis une question dont le premier résultat se détache nettement des suivants (écart de score visible) : sur le PC Windows, les vecteurs sont calculés en pleine précision sur CPU, et non en demi-précision sur GPU, ce qui peut inverser deux résultats aux scores presque égaux.

- [ ] **Prépare ta clé API Anthropic** (dans ton gestionnaire de mots de passe, pas par mail).

---

## Étape 1 : Vérifier que le PC est prêt

- [ ] **Version de Windows.** Appuie sur `Windows + R`, tape `winver`, Entrée. Il faut Windows 10 (22H2) ou Windows 11.

- [ ] **Virtualisation activée.** Ouvre le Gestionnaire des tâches (`Ctrl + Maj + Échap`), onglet *Performances*, puis *UC*. Tu dois lire « Virtualisation : Activé ». Sinon, il faut l'activer dans le BIOS.
![alt text](image.png)


- [ ] **Mémoire et disque.** Vise 16 Go de RAM (8 Go, c'est possible mais serré) et au moins 15 Go d'espace libre.

- [ ] **Le port 5432 est libre.** Il sera utilisé par la base de données.

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  netstat -ano | findstr :5432
  ```
  Rien ne doit s'afficher.

- [ ] **Pas de variables Postgres parasites.**

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  Get-ChildItem Env:PG*
  ```
  Rien ne doit s'afficher.

---

## Étape 2 : Installer les outils

- [ ] **Installe Git, uv et VS Code.** `uv` est l'outil qui installera Python et les bibliothèques.

  📍 **Où :** PowerShell normal, n'importe quel dossier
  ```powershell
  winget install -e --id Git.Git
  winget install -e --id astral-sh.uv
  winget install -e --id Microsoft.VisualStudioCode
  ```

- Si WinGet n’apparaît pas correctement installé, exécute ces commanes à partir d’une invite de commandes PowerShell :
 ```powershell
  Install-PackageProvider -Name NuGet -Force | Out-Null
  Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery | Out-Null
  Repair-WinGetPackageManager -Force -Latest
  ```

- [ ] **Installe Docker Desktop.** Il faut ici un PowerShell **administrateur** : clic droit sur PowerShell, puis « Exécuter en tant qu'administrateur ».

  📍 **Où :** PowerShell **administrateur**, n'importe quel dossier
  ```powershell
  winget install -e --id Docker.DockerDesktop
  ```
  Redémarre Windows si on te le demande.

- [ ] **Lance Docker Desktop** depuis le menu Démarrer. Accepte les conditions d'utilisation et attends que l'icône de baleine dans la barre des tâches soit stable. Si Windows te demande de mettre à jour WSL, ouvre un PowerShell administrateur et tape `wsl --update`.

- [ ] **Ferme puis rouvre PowerShell**, pour que Windows reconnaisse les nouveaux outils. Vérifie ensuite :

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  git --version
  uv --version
  docker version
  docker compose version
  docker run --rm hello-world
  ```
  Chaque commande doit afficher une version ou un message de succès. Pour `docker version`, tu dois voir à la fois une partie « Client » et une partie « Server ».
  ![alt text](image-1.png)

- [ ] **(Seulement si tu as 16 Go de RAM ou moins) Limite la mémoire de Docker**, pour laisser de la place au modèle d'IA.
  1. Ouvre le Bloc-notes.
  2. Colle ces deux lignes :
     ```ini
     [wsl2]
     memory=4GB
     ```
  3. Enregistre sous `C:\Users\<toi>\.wslconfig`. Choisis « Tous les fichiers » comme type, pour éviter une extension `.txt`.
  4. Applique le changement :

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  wsl --shutdown
  ```
  Puis relance Docker Desktop.

---

## Étape 3 : Récupérer le code

- [ ] **Crée un dossier de travail** et télécharge le projet. On utilise `C:\dev` plutôt que `Documents` ou `Bureau`, qui sont souvent synchronisés par OneDrive. Cette synchronisation bloque des fichiers et fait des erreurs étranges.

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  mkdir C:\dev
  cd C:\dev
  git clone -c core.autocrlf=input https://github.com/VictorCilleros/assistant-regles-w40k.git
  cd assistant-regles-w40k
  ```
  À la fin, ton PowerShell est dans `C:\dev\assistant-regles-w40k`. **Toutes les étapes suivantes se font depuis ce dossier**, sauf mention contraire.

---

## Étape 4 : Installer Python et les bibliothèques

- [ ] **Installe tout d'un coup.** Cette commande télécharge Python 3.13 si besoin, crée un environnement isolé (le dossier `.venv`) et installe les bibliothèques. Compte plusieurs Go de téléchargement et quelques minutes.

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv sync
  ```

- [ ] **Vérifie qu'il n'y a pas de GPU détecté** (c'est normal, on travaille sur CPU).

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
  ```
  Tu dois voir `False` à la fin de la ligne, sinon encore mieux le GPU est utilisable.

---

## Étape 5 : Configurer le fichier `.env`

Le fichier `.env` contient tes secrets (clé API, mot de passe de la base).

Version courte :
- **Copie directement le fichier `.env` de ton PC actuel** à la racine du projet (`C:\dev\assistant-regles-w40k\.env`).
- **Ouvre-le et vérifie la ligne `HF_HUB_OFFLINE`** : elle doit valoir `0` pour le premier lancement (voir la version longue ci-dessous). Sur ton PC actuel, elle vaut probablement `1`, ce qui ferait échouer l'étape 8.
- Passe ensuite aux « Étapes supplémentaires importantes » plus bas dans cette étape 5.

Version longue :
- [ ] **Crée ton `.env` à partir du modèle fourni.**

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  Copy-Item .env.example .env
  code .env
  ```
  La seconde commande ouvre le fichier dans VS Code.

- [ ] **Remplis-le ainsi** (dans VS Code, fichier `C:\dev\assistant-regles-w40k\.env`) :
  ```ini
  ANTHROPIC_API_KEY=sk-ant-...ta clé...
  PGHOST=localhost
  PGPORT=5432
  PGDATABASE=assistant_regles
  PGUSER=assistant_regles
  PGPASSWORD=UnMotDePasseSansCaracteresSpeciaux
  HF_HUB_OFFLINE=0
  ```
  Deux points importants :
  - **`PGPASSWORD`** : utilise uniquement des lettres et des chiffres. Ce mot de passe est enregistré **à la première création de la base** : le changer plus tard causera une erreur (voir « Si ça coince »).
  - **`HF_HUB_OFFLINE=0`** : c'est **temporaire**. Le modèle d'IA doit être téléchargé une première fois. Tu remettras `1` à l'étape 8.

Etapes supplémentaires importantes : 
- [ ] **Active l'affichage correct des accents dans le terminal** (une seule fois).

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
  ```
  Puis **ferme et rouvre PowerShell**, et retourne dans le dossier du projet :
  ```powershell
  cd C:\dev\assistant-regles-w40k
  ```

---

## Étape 6 : Démarrer la base de données

La base de données tourne dans un conteneur Docker. Assure-toi que Docker Desktop est lancé. On va globalement utiliser une image pour supporter pgvector dans une base PostgreSQL.

- [ ] **Démarre-la.**

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  docker compose up -d
  docker compose ps
  ```
  Attends que la colonne d'état affiche **healthy** (10 à 20 secondes, plus long la toute première fois car l'image se télécharge). Si ce n'est pas encore le cas, relance `docker compose ps` quelques secondes plus tard.

![alt text](image-2.png)
---

## Étape 7 : Copier les données

Le dossier `data/` n'existe pas encore : il n'est pas sur GitHub. Tu peux tout faire à la main avec l'Explorateur de fichiers.

- [ ] **Crée les dossiers.** Dans `C:\dev\assistant-regles-w40k`, crée `data`, puis dedans `interim`, puis dedans `chunks`. Crée aussi `data\core-rules`.

- [ ] **Copie `chunks.jsonl`** dans `C:\dev\assistant-regles-w40k\data\interim\chunks\`. Le chemin doit être exactement celui-là, sinon l'étape suivante échoue.

- [ ] **Vérifie que le nom est correct.** Dans l'Explorateur, onglet *Affichage*, coche « Extensions de noms de fichiers ». Le fichier ne doit pas s'appeler `chunks.jsonl.txt`.

- [ ] **(Optionnel) Vérifie que le fichier est complet.**

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  (Get-Content data\interim\chunks\chunks.jsonl | Measure-Object -Line).Lines
  ```
  Le résultat attendu est **207**.

---

## Étape 8 : Indexer (premier lancement)

Cette étape télécharge le modèle d'IA (environ 2,3 Go), transforme les 207 morceaux de texte en vecteurs et les range dans la base. Sur CPU, compte de quelques dizaines de secondes à quelques minutes pour le calcul, plus le temps de téléchargement.

- [ ] **Lance l'indexation.**

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run indexer-regles -v
  ```
  Tu dois voir défiler des messages, dont :
  - `207 chunks lus`
  - `Chargement de BAAI/bge-m3 ... sur cpu`
  - `Indexation terminée : 207 chunks`

  Un avertissement sur les « symlinks » peut apparaître : il est sans gravité.

- [ ] **Repasse en mode hors-ligne.** Ouvre `C:\dev\assistant-regles-w40k\.env` (dans VS Code) et remplace `HF_HUB_OFFLINE=0` par `HF_HUB_OFFLINE=1`. Le modèle est maintenant sur ton disque, plus besoin d'Internet pour lui.

---

## Étape 9 : Vérifier que tout marche

- [ ] **Test de la recherche** (sans appel à l'API, gratuit). Remplace le texte par ta question de test de l'étape 0.

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run chercher-regles "ta question de test" -k 3
  ```
  Compare avec ce que tu as noté sur ton PC actuel : tu dois retrouver **les mêmes codes**, avec le même premier résultat. Les scores peuvent différer très légèrement, et deux résultats aux scores presque égaux peuvent s'inverser : c'est normal (calcul sur CPU en pleine précision, contre GPU en demi-précision).

- [ ] **Test de la réponse complète** (ces deux commandes utilisent ta clé API et coûtent quelques centimes).

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run repondre-regles "ta question de test" --no-agent
  uv run repondre-regles "ta question de test" --agent
  ```

- [ ] **Test de l'interface.**

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run interface-regles
  ```
  **Au tout premier lancement, Streamlit peut demander une adresse e-mail dans PowerShell** : appuie simplement sur Entrée pour passer. Ouvre ensuite <http://localhost:8501> dans ton navigateur. La première question est plus lente (le modèle se charge, environ 10 à 30 secondes), les suivantes sont rapides. Si le pare-feu Windows pose une question, tu peux cliquer sur « Annuler ». Pour arrêter l'interface, appuie sur `Ctrl + C` dans PowerShell.

- [ ] **Si les accents s'affichent mal.**

  📍 **Où :** PowerShell, n'importe quel dossier
  ```powershell
  chcp 65001
  [Console]::OutputEncoding = [Text.Encoding]::UTF8
  ```

---

## Étape 10 : Lancer les tests

- [ ] **Tests rapides** (les seuls lancés par défaut).

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run pytest
  ```

- [ ] **Tests avec Docker** (nécessitent que Docker Desktop tourne).

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run pytest -m integration
  ```
  Si ces tests échouent à cause d'un conteneur nommé « Ryuk », relance-les après avoir tapé, dans le même PowerShell :
  ```powershell
  $env:TESTCONTAINERS_RYUK_DISABLED = "true"
  ```

- [ ] **Tests avec le vrai modèle d'IA** (plus lents sur CPU).

  📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
  ```powershell
  uv run pytest -m modele
  ```

---

## Relancer l'assistant plus tard

Après un redémarrage du PC, la base n'est plus démarrée. Pour retrouver l'assistant :

1. Lance **Docker Desktop** et attends que la baleine soit stable.
2. Démarre la base, puis l'interface :

   📍 **Où :** PowerShell, dossier `C:\dev\assistant-regles-w40k`
   ```powershell
   docker compose up -d
   uv run interface-regles
   ```

Pas besoin de réindexer : les données restent dans la base tant que tu ne lances pas `docker compose down -v`.

---

## Si ça coince

| Ce que tu vois | Ce que ça veut dire | Quoi faire |
|---|---|---|
| Une erreur qui parle de « offline » au chargement du modèle | Le mode hors-ligne est activé alors que le modèle n'est pas encore téléchargé | Dans `.env`, mets `HF_HUB_OFFLINE=0`, relance, puis remets `1` |
| `Base injoignable` | Docker Desktop est éteint, ou la base n'est pas démarrée | Lance Docker Desktop, puis `docker compose up -d` depuis `C:\dev\assistant-regles-w40k` |
| `password authentication failed` | Tu as changé `PGPASSWORD` après la première création de la base | Depuis `C:\dev\assistant-regles-w40k` : `docker compose down -v`, puis `docker compose up -d`, puis relance l'étape 8. Cela efface la base, sans gravité ici puisque tu réindexes |
| `à définir dans .env` | Une ligne manque dans ton `.env` | Compare avec l'étape 5 |
| Erreur « base vide » ou « autre modèle » | L'indexation n'a pas été faite | Relance l'étape 8 |
| `docker: error during connect` | Docker Desktop n'est pas lancé | Ouvre-le et attends que la baleine soit stable |
| Le PC devient très lent | Pas assez de mémoire | Applique la limite de l'étape 2 et ferme les autres programmes |
| Accents illisibles | Le terminal n'est pas en UTF-8 | Voir l'étape 9, dernière case |
| Streamlit demande un e-mail et semble bloqué | Message de bienvenue au premier lancement | Appuie sur Entrée |
| `Port 8501 is already in use` | Une interface tourne déjà (autre fenêtre PowerShell) | Ferme-la avec `Ctrl + C`, ou ouvre <http://localhost:8501> directement |
| L'évaluation ne trouve pas le gold set | Le notebook 08 lit `gold_set.xlsx` | Renomme le fichier ou adapte le chemin dans le notebook |
