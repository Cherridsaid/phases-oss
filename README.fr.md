[English](README.md) · **Français**

# phases-oss

[![tests](https://github.com/Cherridsaid/phases-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/Cherridsaid/phases-oss/actions/workflows/ci.yml)

![phases-oss — garde-fous pour agents de code IA](docs/social.png)

Un exécuteur de travail par phases pour agents de code IA — phases bornées,
preuves déterministes, un relecteur consultatif, et un moteur d'analyse
**Multidim** intégré (analyse structurée multi-axes avant d'écrire du code).
Bibliothèque standard Python pure, **zéro dépendance**.

`phases-oss` aide un agent (ou une personne) à mener un travail risqué par
petites étapes vérifiables, au lieu d'un seul grand changement non audité.

---

## L'idée

Découper une tâche en **phases**. Chaque phase déclare, d'entrée :

| Champ | Signification |
|-------|---------------|
| **objectif** | un but unique, testable |
| **files_allowed** | les seuls fichiers que cette phase peut toucher |
| **proof_command** | une commande reproductible ; code de sortie 0 = ça marche |
| **niveau de risque** | `0`–`3` ; normalise toute la politique (voir ci-dessous) |

Le **niveau de risque** règle toutes les portes d'un coup (les anciens noms
`none` / `review` / `security` / `critical` restent acceptés comme alias de 0–3) :

| Niveau | Preuve | Revue | Portes supplémentaires à la clôture |
|--------|--------|-------|-------------------------------------|
| **0** | ciblée | aucune | — |
| **1** | standard | une revue indépendante | — |
| **2** | **suite de tests complète** (`--full-suite` requis à l'init) | une revue stricte | suite complète déclarée |
| **3** | suite de tests complète | une revue stricte | + preuve d'exécution + **validation humaine explicite** (`human-approve`) |

Une phase traverse des portes explicites :

```
init → approve → (écrire le code) → prove → audit → [review] → [human-approve] → close
```

* **prove** exécute la commande de preuve ; son code de sortie fait seul autorité.
* **close** rejoue la preuve contre l'arbre *commité* (un worktree git jetable),
  pour qu'une copie de travail verte ne puisse pas passer pour un commit vert.
  Cette vérification indépendante n'a lieu que si un sha de commit est enregistré
  (`--commit-sha`) ; clôturer sans lui la saute — passez toujours le sha.
* une phase ne **clôture** pas tant que sa preuve n'est pas passée, son audit
  enregistré, et chaque porte propre au niveau satisfaite.

L'état vit dans `<repo>/.claude/phase-state.json`. Chaque transition ajoute un
**événement v2 reconstructible** à `<repo>/.claude/phase-log.jsonl` : chaque
ligne porte `schema_version`, `event_id`, `phase_id`, `session_id`,
`project_id`, `review_id`/`finding_id` (nul hors d'une revue), un horodatage
UTC, l'`event_type`, et une charge utile qui capture l'état complet — le journal
seul peut reconstruire la phase. Les deux fichiers sont locaux (ignorés par git).

### Porte d'analyse pré-phase (optionnelle, fermée par défaut)

Une phase peut exiger une analyse pré-phase structurée (par exemple une grille
d'analyse multi-dimensionnelle) avant d'écrire la moindre ligne :

```bash
python -m phases_oss.phases init ... --require-analysis \
  --analysis-context code_audit --analysis-depth core \
  --analysis-axes "surface,risques" \
  --analysis-ref "artifact://analysis/md_0123456789abcdef01234567"
```

La profondeur attendue suit le niveau (`core` en 0–1, `deep` en 2, `full` en 3).
Des métadonnées absentes ou mal formées font refuser `init` ; une phase
initialisée avec `--require-analysis` refuse de **clôturer** si les métadonnées
ont disparu. Le journal enregistre un événement `analysis.completed` avec les
métadonnées et la seule référence à l'artefact — le texte de l'analyse n'est
jamais recopié dans le journal.

## Modèle de menace honnête — à lire en premier

L'outillage local est ici une **aide à la discipline, pas une frontière de
sécurité.**

Un agent et son relecteur tournent sur la même machine avec les mêmes droits.
Aucun verrou local (un hook, un secret, une empreinte) n'empêche un processus
déterminé de modifier le fichier d'état et de lever toutes les restrictions. Les
hooks **échouent ouvert** délibérément, pour ne jamais bloquer une session sans
rapport.

L'autorité réelle est ailleurs :

- **des tests déterministes** — code de sortie 0, sinon ça n'a pas eu lieu ;
- **une CI** avec protection de branche ;
- **une relecture humaine** avant toute fusion ou publication.

Traitez le relecteur statique comme un linter de discipline de processus, et les
hooks comme des garde-fous qui rattrapent les erreurs de bonne foi — pas comme
un bac à sable.

### Ce que cet outil fait, et ce qu'il ne fait pas

- **Il lit du code local, en lecture seule.** Rien ici n'exploite quoi que ce
  soit, ne scanne un réseau, ne pratique de test d'intrusion, ni n'envoie de
  requête vers un système tiers.
- **Il ne fournit pas les skills.** Il suppose une bibliothèque de skills déjà
  présente sur votre machine et résout les corps de skill *par référence*. Sans
  elle, les phases concernées rendent `missing_skill`. C'est la limite qui
  compte le plus sur une installation neuve.
- **Sans adaptateur de plan modèle branché, les phases de revue guidée rendent
  `degraded` / `model_plane_unavailable`.** Le pipeline orchestre ; il n'analyse
  pas. Lisez un parcours complet comme une séquence ordonnée et traçable — pas
  comme un verdict d'audit.
- **CodeQL reste derrière une porte.** PHASE 22 demeure dans la séquence et
  rend `skipped_license` tant que `--enable-codeql` n'a pas confirmé les termes.
  C'est volontaire.

## Installation

```bash
git clone https://github.com/Cherridsaid/phases-oss
cd phases-oss && pip install -e .
```

(Pas encore sur PyPI ; installation depuis une copie du dépôt.)

Câblez les hooks dans *un seul projet* (jamais dans votre configuration globale) :

```bash
python -m phases_oss.install /chemin/vers/votre-projet          # à blanc, affiche le plan
python -m phases_oss.install /chemin/vers/votre-projet --apply   # écrit réellement
```

L'installateur écrit exactement un fichier, `<projet>/.claude/settings.json`,
fusionne sans rien détruire, et **refuse** de viser votre répertoire personnel
ou `~/.claude`.

## Démarrage rapide (la CLI de phases)

```bash
# 1. déclarer une phase
python -m phases_oss.phases init \
  --objective "add a JSON parser" \
  --files src/parser.py --files tests/test_parser.py \
  --proof "python -m pytest tests/test_parser.py" \
  --level 1

# 2. approuver, puis écrire le code dans files_allowed uniquement
python -m phases_oss.phases approve

# 3. prouver (le code de sortie fait autorité)
python -m phases_oss.phases prove

# 4. enregistrer l'audit, puis clôturer sur un commit
python -m phases_oss.phases audit --report .claude/phase-reviews/r1.md
python -m phases_oss.phases close --lesson "parser handles trailing commas" --commit-sha "$(git rev-parse HEAD)"
```

Voir [`examples/quickstart.md`](examples/quickstart.md) pour un parcours complet.

## Relecteurs

L'étape d'audit peut appeler un **relecteur** :

- **`local`** (par défaut) — un linter statique, à base d'expressions
  régulières, entièrement hors ligne. Aucun modèle, aucun réseau, **aucun LLM**
  sur ce chemin, par construction. Il signale les secrets en dur, les points
  d'arrêt de débogage, les `except:` nus, `shell=True`, `eval`/`exec`, et les
  marqueurs TODO. Un commentaire `# phases-oss: allow` fait sauter une ligne
  relue.
- **`cloud`** (opt-in) — une coquille fine qui délègue à un **émetteur que vous
  câblez vous-même**. Sans émetteur, il est inerte (aucun réseau). Une fois
  câblé, chaque charge utile passe d'abord par une **porte de données** : l'hôte
  de destination doit figurer sur une liste blanche explicite (refus par
  défaut), et la charge est expurgée (secrets, jetons, courriels, noms
  d'utilisateur dans les chemins) avec une divulgation jointe.

Le relecteur cloud **échoue fermé sur indisponibilité** : un backend absent, un
émetteur injoignable, une réponse vide ou illisible donnent tous
`REVIEW_UNAVAILABLE` — jamais un PASS, jamais un saut silencieux. Il existe
quatre verdicts de revue : `PASS` (continuer), `PASS_WITH_NOTES` (continuer,
constats à lire), `REFUS` (corriger et refaire relire), `REVIEW_UNAVAILABLE` (la
revue n'a pas eu lieu). Une fois un verdict enregistré sur la phase, `close` en
dépend : `REFUS` et `REVIEW_UNAVAILABLE` refusent tous deux la clôture jusqu'à
ce qu'une nouvelle revue passe. Les verdicts forment un vocabulaire fermé,
analysé strictement — `VERDICT: PASSABLE` ou une occurrence isolée du mot
`VERDICT` n'approuve rien, et un nouveau `prove` invalide toute validation
enregistrée contre l'arbre précédent.

```python
from phases_oss.reviewers import get_reviewer
reviewer = get_reviewer("local")          # par défaut, hors ligne
```

## Hooks

Trois hooks portent les portes dans un harnais d'agent (`hooks` dans
`settings.json`, façon Claude Code) :

- **PreToolUse** — refuse les éditions hors de `files_allowed`, ainsi que les
  commandes Bash qui écrivent un fichier du projet (les écritures propres à git
  exceptées).
- **Stop** — refuse de « conclure » tant qu'une phase reste ouverte (preuve ou
  audit manquant).
- **UserPromptSubmit** — approuve sur un `go phase` exact, et injecte un rappel
  assaini et *marqué non fiable* de la phase active.

## Multidim (analyse intégrée)

![Multidim — analyse structurée avant d'écrire une ligne](docs/multidim.png)

phases-oss embarque **Multidim**, un petit moteur d'analyse qui transforme un
sujet en une grille hiérarchique (axes → sous-lentilles) que l'appelant
remplit, puis vérifie l'analyse remplie de façon déterministe. La pensée reste
chez l'appelant ; Multidim fournit la structure, pas la cognition. Il tourne
comme son propre serveur MCP stdio et dispose de son propre magasin dédié — il
n'est jamais fusionné dans le moteur de phases.

Il expose quatre outils :

- **`multidim_analyze`** — construit la grille pour un sujet. `format: "text"`
  (grille v1) ou `format: "v2"` (cadre JSON déterministe avec un `frame_hash`,
  des sections requises, des règles de validation et les pièges appris).
- **`multidim_validate`** — vérification déterministe et sans état d'une analyse
  v2 remplie contre son cadre ; rend un verdict `ACCEPT` / `WARNING` / `REJECT`
  par section. Ne modifie jamais le magasin, ne juge que la structure et la
  cohérence interne.
- **`multidim_contexts`** — liste les contextes d'analyse connus.
- **`multidim_learn`** — crée ou enrichit un contexte (l'unique porte d'écriture).

Lancer le serveur directement (c'est ainsi que les clients MCP le démarrent) :

```bash
python -m phases_oss.multidim        # ou le script console : phases-multidim
```

Ou laisser le moteur de phases produire une analyse pour une phase, sans MCP
externe :

```bash
phases prepare-analysis --subject "what you are about to change" --level 2
# affiche contexte / profondeur / axes / analysis-ref à donner à :
phases init --require-analysis --analysis-context ... --analysis-depth ... \
            --analysis-axes ... --analysis-ref artifact://multidim/<id> ...
```

Le magasin vit dans un répertoire de données dédié, propre à la plateforme
(jamais `~/.multidim`), avec écritures atomiques, verrou inter-processus et une
bibliothèque de base neutre. Une liste noire privée pour la garde de neutralité
peut être fournie hors bande via `PHASES_OSS_EXTRA_FORBIDDEN` (séparée par des
virgules), jamais commitée dans les sources.

## Pipeline d'audit (71 phases, un skill chacune)

`phases-audit` parcourt une séquence figée de 71 phases d'audit. **Un skill, une
phase, toujours** — l'ordre est gelé au moment de l'import, et une phase qui ne
s'applique pas est tout de même *visitée* : elle reçoit un statut terminal et
une raison typée, puis la séquence passe à `ordinal + 1`. Rien n'est jamais
écarté.

```bash
phases-audit pipeline                    # la correspondance figée PHASE N -> skill
phases-audit tools                       # quels scanners locaux sont installés
phases-audit run --target ../un-repo     # visite les 71 phases
phases-audit resume run_<id>             # reprend à l'ordinal interrompu
```

Chaque phase tourne dans une aire jetable qui expose exactement un `SKILL.md`,
avec `HOME` repointé sur cette aire ; l'aire est détruite avant que la phase
suivante commence. Les corps de skill sont résolus *par référence* vers vos
racines locales — aucun n'est embarqué ici.

Statuts : `completed`, `not_applicable`, `degraded`, `failed`,
`skipped_license`, `skipped_offline`, `missing_skill`. Les raisons viennent d'un
vocabulaire fermé (`policy_static_only`, `tool_absent`, `signal_absent:<nom>`,
…) afin d'être testables plutôt que seulement lisibles.

Les valeurs par défaut, et leurs limites honnêtes :

* **static_only** — le code de la cible n'est pas exécuté. Passez
  `--allow-local-test-execution` pour le lancer dans une copie éphémère.
* **CodeQL est derrière une porte** — PHASE 22 reste dans la séquence mais rend
  `skipped_license` tant que `--enable-codeql` n'a pas confirmé les termes.
* **Aucun téléchargement** — les commandes ne portent aucun drapeau de mise à
  jour ou de registre, et semgrep refuse `--config auto` ; sans pack de règles
  local, la phase rend `tool_absent` au lieu d'aller en ligne.
* **Les secrets sont retirés** — les identifiants de fournisseurs et de
  registres sont ôtés de tout environnement du plan d'exécution.
* **La cible est en lecture seule** — empreintée avant et après ; une mutation
  est un échec dur.
* **L'isolation réseau est `advisory`, pas imposée.** Des variables de proxy
  pointent vers un port fermé, ce qui arrête les clients HTTP bien élevés. Il
  n'y a pas ici d'espace de noms réseau par processus, donc une socket brute
  n'est *pas* bloquée. Le parcours annonce `advisory` ; il ne prétend jamais
  être hors ligne.
* **Les corps de skill manquants sont signalés, jamais substitués**
  (`missing_skill`).

Rien dans ce pipeline ne publie : `open-source-readiness` et
`release-readiness` rendent un verdict et s'arrêtent. Aucun remote, aucun push,
aucune publication.

## Développement

```bash
python run_tests.py     # unittest de la bibliothèque standard, sortie 0 = vert
```

Aucune dépendance, aucune étape de construction. Python 3.9+ (l'intervalle que
la CI prouve).

## Licence

Apache-2.0. Voir [LICENSE](LICENSE).
