> **Spécification de travail, pas un mode d'emploi.** Le moteur d'analyse décrit ici
> (adaptateurs de plan modèle, schémas JSON figés, vérification fichier:ligne,
> revue adversariale post-run) n'est **PAS livré dans ce dépôt**. Le point d'injection
> existe (`SubprocessAdapter`, `runner.py`) mais aucune commande ne l'expose aujourd'hui :
> `phases-audit run` rend `degraded`. Ce document décrit une direction, pas l'état du
> code publié.

# GO UNIQUE — PHASES-OSS COMPLET, RÉELLEMENT ANALYSANT ET AVEC REVUE ADVERSARIALE FINALE

## 71 SKILLS = 71 PHASES = 1 SKILL PAR PHASE

## MODEL PLANE RÉEL + SCHÉMAS JSON FIGÉS + PREUVES FICHIER:LIGNE + REVUE ADVERSARIALE APRÈS PHASE 71

Tu dois poursuivre le travail sur `phases-oss` à partir de son état actuel.

Ce prompt constitue **un seul GO pour l’ensemble du cycle**.

Tu peux organiser ton travail interne en autant de tâches techniques que nécessaire, mais tu ne dois pas me demander de recoller un nouveau prompt ou de valider chaque tâche intermédiaire.

Tu ne t’arrêtes avant la fin que devant un **blocage technique réel** qui ne peut pas être résolu proprement sans :

```text
téléchargement ou installation non autorisée
credential absente
modification globale de la sécurité de la machine
décision juridique
action sur une ressource externe
intervention explicite de l'utilisateur
```

# 1. ÉTAT ACTUEL À CONSIDÉRER COMME POINT DE DÉPART

La structure du produit est validée :

```text
71 skills
=
71 phases
=
1 skill exactement par phase
=
ordre canonique figé
```

Le dernier run réel sur `phases-oss` a donné :

```text
degraded           = 49
not_applicable      = 15
skipped_offline     = 5
skipped_license     = 1
failed              = 1
completed           = 0
```

La conclusion correcte est :

```text
0 phase completed
0 analyse réelle
0 conclusion possible sur la sécurité du code
```

Cela ne signifie absolument pas :

```text
0 vulnérabilité
```

Cela signifie :

```text
aucune analyse effective n'a encore été exécutée
```

Le pipeline structurel fonctionne.

Le problème est désormais fonctionnel.

# 2. CAUSE PRINCIPALE CONNUE

Les 49 phases de revue guidée utilisent des `SKILL.md`.

Un `SKILL.md` n’est pas un programme autonome.

Il contient des instructions qui doivent être lues et appliquées par un véritable modèle.

L’état actuel utilise par défaut un comportement équivalent à :

```python
def null_adapter(spec, stage, target):
    return None
```

puis :

```python
outcome = self.adapter(spec, stage, self.target)

if outcome is None:
    return PhaseOutcome(
        DEGRADED,
        "model_plane_unavailable",
        ...
    )
```

L’orchestrateur :

```text
prépare la phase
expose le bon SKILL.md
prépare la cible
```

mais aucun modèle ne réalise ensuite l’analyse.

C’est la cause principale des :

```text
49 × degraded / model_plane_unavailable
```

# 3. OBJECTIF GLOBAL DE CETTE MISSION

Passer de :

```text
71 phases orchestrées
0 analyse effective
```

à :

```text
71 phases orchestrées
+
phases applicables réellement exécutées
+
Model Plane réellement branché
+
outils déterministes réellement exécutés lorsqu'ils sont disponibles
+
findings réels
+
preuves mécaniquement vérifiées
+
consolidation
+
revue adversariale finale indépendante
```

Ne rajoute aucun nouveau skill.

Ne rajoute aucune nouvelle phase.

Ne change aucun ordinal.

Le problème n’est plus le nombre de skills.

Le problème est de :

> **brancher réellement les moteurs existants, vérifier les preuves et confronter le résultat final à une revue adversariale après les 71 phases.**

# 4. RÈGLE ABSOLUE DU WORKFLOW PRINCIPAL

```text
1 SKILL = 1 PHASE
```

Donc :

```text
71 SKILLS = 71 PHASES
```

Cette architecture est non négociable.

Il est interdit de transformer le produit final en :

```text
10 macro-phases
16 macro-phases
lots
groupes
combos
lanes parallèles
sous-pipelines concurrents
```

Tu peux avoir des tâches techniques internes de construction.

Elles ne deviennent jamais des phases du produit.

# 5. EXCEPTION UNIQUE — REVUE ADVERSARIALE APRÈS PHASE 71

Il existe **une seule exception** au workflow des skills analysants.

Cette exception est :

```text
POST-RUN ADVERSARIAL REVIEW
```

ou :

```text
REVUE ADVERSARIALE FINALE
```

Cette revue :

```text
n'est PAS un skill
n'est PAS une phase
n'est PAS PHASE 72
n'a PAS d'ordinal
n'est PAS insérée entre deux phases
```

Elle ne doit jamais être exécutée :

```text
entre PHASE 01 et PHASE 02
entre PHASE 20 et PHASE 21
entre PHASE 64 et PHASE 65
entre PHASE 70 et PHASE 71
```

Elle se déclenche uniquement lorsque :

```text
PHASE 71
=
second-opinion
```

a terminé avec un statut terminal valide.

Ordre absolu :

```text
PHASE 01
↓
PHASE 02
↓
...
↓
PHASE 70
↓
PHASE 71
↓
validation de PHASE 71
↓
fermeture du workflow 71 phases
↓
REVUE ADVERSARIALE FINALE
↓
rapport final
```

La revue adversariale est donc une **couche post-run**.

Elle ne modifie jamais la règle :

```text
71 skills = 71 phases
```

# 6. POURQUOI LA REVUE ADVERSARIALE EST UNE EXCEPTION

Les 71 phases produisent l’audit.

La revue adversariale ne constitue pas un nouveau contrôle spécialisé du catalogue.

Son rôle est de **challenger le résultat de l’ensemble du workflow**.

Elle doit chercher notamment :

```text
findings insuffisamment prouvés
faux positifs encore présents
contradictions entre phases
sévérités surévaluées
sévérités sous-évaluées
consolidations incorrectes
doublons non fusionnés
findings fusionnés à tort
preuves faibles
preuves contradictoires
fichiers ou lignes mal attribués
zones importantes jamais réellement analysées
phases annoncées completed mais peu substantielles
limitations minimisées
risques potentiellement manqués
angles morts de l'audit
```

Elle ne remplace jamais une phase.

Elle évalue le résultat collectif **après sa production complète**.

# 7. LA REVUE ADVERSARIALE NE MODIFIE PAS L’HISTORIQUE DES 71 PHASES

Une fois PHASE 71 terminée :

```text
PHASE 01 → statut figé
PHASE 02 → statut figé
...
PHASE 71 → statut figé
```

La revue adversariale ne peut pas réécrire rétroactivement :

```text
completed
not_applicable
degraded
failed
skipped_license
skipped_offline
```

Elle peut uniquement produire :

```text
challenges
observations
candidate_findings
disputed_findings
coverage_gaps
recommendations
```

Le run des 71 phases reste historiquement intact.

# 8. NOUVEAU FINDING DÉTECTÉ PAR LA REVUE ADVERSARIALE

Si la revue adversariale identifie un nouveau problème potentiel, elle ne peut pas simplement l’ajouter au rapport comme vulnérabilité confirmée.

Le nouveau problème devient :

```text
adversarial candidate finding
```

Il doit passer exactement les mêmes contrôles que les autres findings :

```text
JSON Schema validation
↓
fichier réel
↓
start_line réelle
↓
end_line réelle
↓
preuve correspondant réellement au contenu
↓
FindingEvidenceVerifier
```

Seulement après ces vérifications, il peut devenir :

```text
adversarial finding accepted
```

Il reste identifié comme provenant de :

```text
source = post_run_adversarial_review
```

et ne doit pas être faussement attribué à une des 71 phases.

# 9. LA REVUE ADVERSARIALE NE DOIT PAS ÊTRE AUTORÉFÉRENTIELLE

La revue adversariale doit être exécutée dans :

```text
nouvelle session
contexte propre
aucune mémoire de raisonnement précédente
```

Elle reçoit uniquement les données nécessaires :

```text
pipeline.json
run-state.json
stage envelopes
findings validés
rapport consolidé
limitations
matrice d'applicabilité
statuts des 71 phases
artefacts autorisés
cible locale read-only si nécessaire
```

Elle ne reçoit jamais les chaînes de raisonnement internes des appels précédents.

Elle doit challenger les **preuves et résultats**, pas reproduire la réflexion interne des modèles précédents.

# 10. PROVIDER DE LA REVUE ADVERSARIALE

La revue adversariale utilise un provider explicitement configuré.

Elle peut utiliser :

```text
Claude
Codex
```

selon la configuration disponible.

Ne change jamais automatiquement de fournisseur sans configuration ou autorisation.

Lorsqu’un fournisseur alternatif est explicitement disponible et autorisé, l’architecture peut permettre une revue adversariale par un modèle différent du modèle principal afin d’obtenir une confrontation plus indépendante.

Mais :

```text
pas de bascule silencieuse
pas de credential improvisée
pas de provider choisi arbitrairement
```

Enregistrer :

```text
adversarial_provider
adversarial_model
started_at
finished_at
```

# 11. SCHÉMA JSON ADVERSARIAL FIGÉ

Comme pour les revues guidées, la revue adversariale doit avoir son propre schéma JSON figé **avant son exécution**.

Créer :

```text
adversarial-review-output.schema.json
```

avant le premier appel adversarial.

Le schéma doit notamment prévoir :

```text
schema_version
summary
reviewed_findings
disputed_findings
confirmed_findings
candidate_findings
coverage_gaps
contradictions
limitations
final_challenge_assessment
```

Les nouveaux `candidate_findings` doivent contenir obligatoirement :

```text
finding_id
title
severity
confidence
cwe
description
file
start_line
end_line
evidence
recommendation
```

Le schéma doit être :

```text
versionné
hashé
strict
```

avec :

```text
additionalProperties = false
```

lorsque raisonnablement possible.

# 12. DEUX RÈGLES ABSOLUES SUR LES FINDINGS

## RÈGLE A — CHAQUE FINDING DOIT ÊTRE MÉCANIQUEMENT VÉRIFIÉ FICHIER:LIGNE

Aucun finding produit par Claude, Codex ou la revue adversariale ne peut être accepté uniquement parce que le modèle l’affirme.

Chaque finding doit fournir :

```text
file
start_line
end_line
evidence
```

PHASES vérifie mécaniquement :

```text
le fichier existe réellement
le chemin reste dans la cible
start_line existe
end_line existe
start_line <= end_line
la plage existe réellement
la preuve correspond au contenu de cette plage
```

Un finding qui échoue :

```text
finding rejected
reason = evidence_verification_failed
```

Il n’entre pas dans le consolidateur final.

## RÈGLE B — SCHÉMA JSON FIGÉ AVANT LE PREMIER APPEL MODÈLE

Avant toute phase Model Plane, créer :

```text
guided-review-output.schema.json
```

Le schéma est figé avant le premier appel.

Toutes les phases model-driven utilisent ce contrat.

Le premier appel modèle ne peut avoir lieu que lorsque :

```text
schema exists
schema validates
schema_version recorded
schema_sha256 recorded
```

Une sortie non conforme est rejetée.

Ne jamais la « comprendre quand même ».

# 13. SCHÉMA JSON CANONIQUE DES REVUES GUIDÉES

Structure minimale :

```json
{
  "schema_version": 1,
  "phase_id": "PHASE 07",
  "skill_id": "code-review",
  "summary": "string",
  "analyzed_files": [
    "relative/path/file.py"
  ],
  "findings": [
    {
      "finding_id": "string",
      "title": "string",
      "severity": "critical|high|medium|low|info",
      "confidence": "high|medium|low",
      "cwe": "CWE-XXX|null",
      "description": "string",
      "file": "relative/path/file.py",
      "start_line": 10,
      "end_line": 15,
      "evidence": "exact relevant source excerpt",
      "recommendation": "string"
    }
  ],
  "limitations": []
}
```

Le véritable JSON Schema doit être strict.

# 14. FINDING SANS LOCALISATION = PAS DE FINDING ACCEPTÉ

Un finding accepté doit être rattachable mécaniquement à du contenu local.

Obligatoire :

```text
file
start_line
end_line
evidence
```

Une observation générale sans localisation vérifiable peut apparaître dans :

```text
summary
limitations
coverage_gaps
```

mais ne doit pas devenir une vulnérabilité confirmée.

# 15. FINDING EVIDENCE VERIFIER

Implémenter un composant déterministe :

```text
FindingEvidenceVerifier
```

Pour chaque finding :

```text
normaliser chemin
rejeter path traversal
vérifier fichier
vérifier lignes
extraire lignes
comparer evidence
calculer hash
```

Produire :

```text
evidence_verified
evidence_sha256
source_file_sha256
```

Ces valeurs sont calculées par PHASES.

Jamais auto-certifiées par le modèle.

# 16. CHAÎNE DE CONFIANCE DES FINDINGS

Architecture obligatoire :

```text
MODEL
↓
candidate findings
↓
JSON Schema Validator
↓
FindingEvidenceVerifier
↓
accepted findings
↓
SARIF adapter
↓
findings-consolidator
```

Pour la revue adversariale :

```text
ADVERSARIAL MODEL
↓
adversarial candidate findings
↓
Adversarial JSON Schema Validator
↓
FindingEvidenceVerifier
↓
accepted adversarial findings
↓
final report
```

# 17. PIPELINE CANONIQUE — ORDRE FIGÉ

```text
PHASE 01 = target-inventory
PHASE 02 = architecture-review
PHASE 03 = audit-context-building
PHASE 04 = audit-prep-assistant
PHASE 05 = entry-point-analyzer
PHASE 06 = security-threat-model

PHASE 07 = code-review
PHASE 08 = find-bugs
PHASE 09 = code-maturity-assessor
PHASE 10 = differential-review
PHASE 11 = coverage-analysis

PHASE 12 = quality-gate
PHASE 13 = qa
PHASE 14 = mutation-testing
PHASE 15 = property-based-testing
PHASE 16 = fuzz-testing
PHASE 17 = run-acceptance-tests
PHASE 18 = playwright
PHASE 19 = performance-review
PHASE 20 = accessibility-review

PHASE 21 = semgrep
PHASE 22 = codeql
PHASE 23 = secret-scanner
PHASE 24 = security-review
PHASE 25 = audit-securite
PHASE 26 = security-best-practices
PHASE 27 = insecure-defaults
PHASE 28 = variant-analysis
PHASE 29 = shannon
PHASE 30 = fp-check
PHASE 31 = reachability-triage
PHASE 32 = constant-time-analysis
PHASE 33 = constant-time-testing
PHASE 34 = crypto-review

PHASE 35 = auth-review
PHASE 36 = authorization-review
PHASE 37 = session-security
PHASE 38 = account-recovery-review
PHASE 39 = api-security-review
PHASE 40 = business-logic-review
PHASE 41 = client-side-security-review
PHASE 42 = webhook-security-review

PHASE 43 = data-security-review
PHASE 44 = privacy-review
PHASE 45 = third-party-integration-review
PHASE 46 = generated-code-review
PHASE 47 = tenant-isolation-review
PHASE 48 = billing-entitlement-review
PHASE 49 = commerce-security-review
PHASE 50 = shopify-integration-review
PHASE 51 = mobile-security-review
PHASE 52 = app-store-compliance
PHASE 53 = cloud-runtime-review
PHASE 54 = ai-security-review

PHASE 55 = dependency-vuln-scan
PHASE 56 = supply-chain-risk-auditor
PHASE 57 = sbom-generator
PHASE 58 = license-audit
PHASE 59 = iac-security
PHASE 60 = gha-security-review
PHASE 61 = secure-workflow-guide
PHASE 62 = security-ownership-map

PHASE 63 = sarif-parsing
PHASE 64 = findings-consolidator
PHASE 65 = remediation-advisor

PHASE 66 = operational-readiness
PHASE 67 = open-source-readiness
PHASE 68 = compliance-scope-review

PHASE 69 = retest-findings
PHASE 70 = release-readiness
PHASE 71 = second-opinion
```

Puis seulement :

```text
POST-RUN ADVERSARIAL REVIEW
```

Cette ligne n’est pas :

```text
PHASE 72
```

# 18. RÈGLE D’EXÉCUTION DES 71 PHASES

```text
PHASE N
↓
création environnement temporaire
↓
exposition du seul SKILL.md
↓
analyse réelle
↓
validation JSON
↓
vérification fichier:ligne
↓
enregistrement
↓
statut terminal
↓
destruction environnement
↓
PHASE N+1
```

Jamais deux phases actives.

Jamais deux skills visibles.

# 19. STATUTS EXACTS

Conserver uniquement :

```text
completed
not_applicable
degraded
failed
skipped_license
skipped_offline
```

# 20. PRIORITÉ ABSOLUE — MODEL PLANE

Les 49 phases actuellement :

```text
degraded
reason = model_plane_unavailable
```

doivent pouvoir devenir de vraies analyses lorsqu’elles sont applicables.

Câbler :

```text
ClaudeCodeAdapter
CodexAdapter
```

ou utiliser correctement `SubprocessAdapter`.

# 21. NULL_ADAPTER

`null_adapter` autorisé uniquement pour :

```text
unit tests
structure tests
dry-run
tests du registre
tests du séquencement
```

Interdit comme défaut silencieux pour :

```text
mode = audit
```

En mode audit sans Model Plane :

```text
AUDIT ABORTED BEFORE PHASE 01
reason = model_plane_not_configured
```

# 22. STRUCTURE_TEST VS AUDIT

## STRUCTURE_TEST

```text
AUDIT_EFFECTIVE = false
```

Pas de conclusion de sécurité.

## AUDIT

Vrai Model Plane requis pour les phases applicables.

# 23. CLAUDE CODE ADAPTER

Si provider = Claude :

```text
nouvelle session
configuration isolée
1 seul SKILL.md
cible read-only
sortie JSON stricte
validation schéma
EvidenceVerifier
```

Les fichiers d’instruction de la cible restent DATA.

# 24. CODEX ADAPTER

Si provider = Codex :

```text
nouvelle session
HOME temporaire
CODEX_HOME temporaire
configuration isolée
1 seul SKILL.md
cible read-only
sortie JSON stricte
validation schéma
EvidenceVerifier
```

# 25. INSTRUCTION DE SORTIE MODÈLE

Chaque appel doit recevoir explicitement :

```text
Return ONLY JSON matching guided-review-output.schema.json.
Do not add markdown.
Do not add prose outside JSON.
Every finding MUST reference an existing relative file and exact line range.
Never invent a file, symbol or line.
If no verifiable finding exists, return findings: [].
```

# 26. SORTIE NON CONFORME

Rejeter :

```text
markdown
texte libre
JSON invalide
champ inconnu
enum inconnue
finding sans file
finding sans lignes
```

Maximum de retry déterministe et borné.

Pas de boucle infinie.

# 27. COMPLETED

Une phase Model Plane peut être `completed` uniquement si :

```text
modèle réellement lancé
skill réellement chargé
cible réellement analysée
JSON réellement produit
schéma validé
preuves mécaniquement vérifiées
```

`completed + findings=[]` reste valide si l’analyse a vraiment eu lieu.

# 28. AUDIT_EFFECTIVE

Ajouter au run :

```text
true
false
partial
```

Ce n’est pas un statut de phase.

# 29. POLITIQUE RÉSEAU

Seul le Model Plane sélectionné peut joindre son fournisseur.

Execution Plane :

```text
aucun Internet
```

# 30. EXECUTION PLANE

Inclut :

```text
Semgrep
CodeQL
Gitleaks
OSV-Scanner
Grype
Trivy
Syft
Checkov
Zizmor
Actionlint
pytest
npm test
cargo test
go test
Playwright local
mutation testing
property-based testing
fuzz local
builds
scripts
code cible
```

Aucun téléchargement pendant `phases scan`.

# 31. SECRETS

Nettoyer notamment :

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
GITHUB_TOKEN
GH_TOKEN
NPM_TOKEN
PYPI_TOKEN
AWS_*
AZURE_*
GCP_*
```

des processus qui n’en ont pas besoin.

# 32. SEMGREP

État actuel :

```text
Semgrep installé
règles locales présentes
environ 2183 observées actuellement
semgrep-core bloqué par WDAC
WinError 4551
```

Ne jamais coder 2183.

Calculer :

```text
rules_discovered
rules_valid
rules_loaded
rules_failed
```

# 33. SEMGREP SOUS WINDOWS

Ne désactive pas WDAC globalement.

Tester d’abord :

```text
Docker
Podman
VM Linux locale
```

avec :

```text
network = NONE
target read-only
rules read-only
--metrics=off
aucun --config auto
aucun téléchargement
```

Sinon :

```text
failed
reason = execution_blocked_by_platform_policy
```

# 34. --CONFIG AUTO INTERDIT

Ne réintroduis jamais :

```text
--config auto
```

dans un run offline.

# 35. OUTILS ABSENTS

État actuel :

```text
gitleaks absent
osv-scanner absent
syft absent
checkov absent
zizmor absent
```

Pendant audit :

```text
skipped_offline
```

Pas d’installation automatique.

# 36. PROVISIONING

```text
00 provisioning
```

reste hors des 71 phases.

Aucune installation pendant `phases scan`.

# 37. CODEQL

PHASE 22 reste derrière :

```text
--enable-codeql
codeql_license_confirmed = true
```

Sinon :

```text
skipped_license
```

# 38. NOT_APPLICABLE

Ne transforme jamais artificiellement une phase non pertinente en `completed`.

# 39. STATIC_ONLY

Mode par défaut.

Les phases nécessitant exécution peuvent devenir :

```text
not_applicable
reason = policy_static_only
```

# 40. LOCAL_TEST_EXECUTION

Activation :

```text
--allow-local-test-execution
```

Copie éphémère + sandbox sans Internet.

# 41. CORPS RÉELS DES 71 SKILLS

Ne jamais :

```text
inventer
renommer
fusionner
remplacer
supprimer
ajouter un 72e skill
```

Résoudre :

```text
ordinal
skill_id
resolved_path
type
sha256
```

# 42. TEST MODEL PLANE AVANT LES 49 APPELS

Commencer par :

```text
PHASE 07 = code-review
```

sur fixture connue.

Preuve :

```text
modèle démarré
skill transmis
fichier lu
JSON conforme
finding connu détecté
fichier:ligne vérifié
phase completed
```

# 43. FIXTURE VOLONTAIREMENT VULNÉRABLE

Mesurer :

```text
défauts attendus
défauts détectés
défauts manqués
faux positifs
findings rejetés pour preuve invalide
```

# 44. VERTICAL SLICE

Tester :

```text
PHASE 01 target-inventory
PHASE 07 code-review
PHASE 08 find-bugs
PHASE 21 semgrep si opérationnel
PHASE 24 security-review
PHASE 31 reachability-triage
PHASE 64 findings-consolidator
PHASE 71 second-opinion
```

Puis seulement, pour tester aussi l’exception :

```text
POST-RUN ADVERSARIAL REVIEW
```

La vertical slice reste un test.

Elle ne modifie pas le pipeline canonique.

# 45. PREUVE MINIMALE AVANT RUN COMPLET

Obtenir :

```text
1 phase Model Plane completed
1 appel modèle réel
1 fichier analysé
1 finding connu détecté
1 preuve fichier:ligne validée
1 JSON conforme
1 finding transmis au consolidateur
```

Puis tester la revue adversariale.

# 46. FINDINGS CONSOLIDATOR

PHASE 64 ne reçoit que les findings ayant passé :

```text
schema validation
+
evidence verification
```

# 47. SECOND-OPINION N’EST PAS LA REVUE ADVERSARIALE

Important :

```text
PHASE 71 = second-opinion
```

reste un skill normal du workflow.

Il est exécuté exactement comme les 70 autres phases.

La :

```text
POST-RUN ADVERSARIAL REVIEW
```

est différente.

Elle intervient **après PHASE 71**.

Ne fusionne jamais les deux notions.

# 48. BUG SARIF À CONSERVER EN TEST

Conserver le test de non-régression concernant :

```text
sarif._strictest
```

et le générateur consommé.

# 49. BUG ROUTEUR EXPO / EXPORT

Conserver :

```text
expo != export
```

comme test de non-régression.

# 50. DISTINGUER BUGS HARNAIS / FINDINGS AUDIT

```text
bugs trouvés dans phases-oss
≠
findings produits sur la cible
```

# 51. RAPPORT APRÈS LES 71 PHASES

Avant la revue adversariale, produire un état :

```text
pre_adversarial_report
```

contenant :

```text
statuts des 71 phases
findings validés
findings consolidés
limitations
coverage
provider/model
outils exécutés
```

Ce document constitue l’entrée principale de la revue adversariale.

# 52. RAPPORT APRÈS REVUE ADVERSARIALE

Produire ensuite :

```text
final_report
```

distinguant clairement :

```text
findings issus des 71 phases
findings confirmés par adversarial review
findings contestés par adversarial review
nouveaux candidate findings adversariaux
nouveaux findings adversariaux validés mécaniquement
coverage gaps
contradictions
limitations
```

Ne mélange pas les provenances.

# 53. FORMULATION OBLIGATOIRE

Si :

```text
completed = 0
```

écrire :

```text
Aucune analyse effective n'a été exécutée.
Aucune conclusion ne peut être tirée sur la sécurité de la cible.
```

Si :

```text
completed > 0
findings = 0
```

écrire :

```text
Aucune vulnérabilité n'a été détectée par les analyses effectivement exécutées.
```

Ne jamais écrire :

```text
Le code est sécurisé.
```

# 55. PREUVE MACHINE-READABLE

Conserver :

```text
pipeline.json
run-state.json
guided-review-output.schema.json
adversarial-review-output.schema.json
```

`pipeline.json` contient exactement :

```text
71 entrées
```

Pas 72.

La revue adversariale doit avoir son propre artefact, par exemple :

```text
adversarial-review.json
```

mais ne doit pas être ajoutée comme ordinal.

# 56. TESTS OBLIGATOIRES SUPPLÉMENTAIRES

Ajouter notamment :

```text
pipeline phases = 71 exactement
adversarial review non présente dans pipeline.json comme phase
aucune PHASE 72
adversarial review impossible avant terminaison PHASE 71
adversarial review exécutée après PHASE 71
adversarial review ne modifie pas les statuts historiques
adversarial schema figé avant appel
adversarial candidate finding sans preuve rejeté
adversarial candidate finding fichier inexistant rejeté
adversarial candidate finding ligne inexistante rejeté
adversarial accepted finding possède evidence_verified=true
provenance post_run_adversarial_review conservée
```

Plus :

```text
audit mode refuse null_adapter
schema guided figé avant premier appel
schema hash stable
sortie hors schéma rejetée
finding sans file rejeté
finding sans lignes rejeté
path traversal rejeté
preuve incorrecte rejetée
finding non vérifié absent du consolidateur
phase completed possible avec findings=[]
provider/model enregistrés
--config auto interdit offline
sarif._strictest non-régression
expo != export
```

# 58. AUCUNE PUBLICATION

```text
aucun remote ajouté
aucun push
aucune release
aucune publication
aucune création de dépôt distant
```

# 59. ORDRE DE TRAVAIL — UN SEUL GO

Exécuter sans me redemander un nouveau prompt :

```text
T1
vérifier l'état actuel

T2
figer guided-review-output.schema.json

T3
figer adversarial-review-output.schema.json

T4
hasher et tester les deux schémas

T5
implémenter FindingEvidenceVerifier

T6
ajouter les tests fichier:ligne/preuve

T7
câbler réellement le Model Plane

T8
implémenter/valider ClaudeCodeAdapter et CodexAdapter

T9
interdire null_adapter en mode audit

T10
ajouter le preflight provider

T11
faire réussir PHASE 07 sur fixture

T12
obtenir un finding connu

T13
vérifier mécaniquement fichier:ligne

T14
transmettre uniquement les findings validés au consolidateur

T15
faire fonctionner Semgrep ou établir le blocage précis

T16
exécuter la vertical slice

T17
corriger les défauts découverts

T18
faire passer tous les tests

T19
lancer le run complet PHASE 01 → PHASE 71

T20
figer pre_adversarial_report

T21
exécuter POST-RUN ADVERSARIAL REVIEW

T22
vérifier mécaniquement les nouveaux candidate findings adversariaux

T23
produire final_report

T24
produire le bilan complet
```

Ces T1–T24 sont des tâches de travail.

Elles ne sont PAS les phases du produit.

# 60. NE PAS ME REDEMANDER UN GO ENTRE LES TÂCHES

Ce prompt constitue le GO complet.

Continuer jusqu’à la fin sauf blocage externe réel.

# 61. RAPPORT FINAL ATTENDU

Je veux notamment :

```text
AUDIT_EFFECTIVE

phases attendues = 71
phases visitées = 71

completed
degraded
not_applicable
skipped_offline
skipped_license
failed

findings proposés par modèles
findings rejetés par schema
findings rejetés par EvidenceVerifier
findings acceptés
findings consolidés

provider/model par phase

Semgrep :
rules_discovered
rules_valid
rules_loaded
rules_failed
findings

skills résolus
skills manquants

outils disponibles
outils absents

guided_schema_version
guided_schema_sha256

adversarial_schema_version
adversarial_schema_sha256

adversarial_provider
adversarial_model

adversarial_review :
findings examinés
findings confirmés
findings contestés
candidate findings
candidate findings rejetés
nouveaux findings mécaniquement validés
coverage gaps
contradictions

durée des 71 phases
durée revue adversariale
durée totale
```

# 62. CRITÈRE DE RÉUSSITE

La réussite n’est plus :

```text
71 phases visitées
```

Cela est déjà prouvé.

La réussite est :

```text
Model Plane réellement branché
+
schéma JSON guided figé
+
schéma JSON adversarial figé
+
phases Model Plane réellement exécutées
+
findings connus détectés
+
fichier:ligne mécaniquement vérifié
+
aucun finding inventé accepté
+
findings consolidés
+
PHASE 71 terminée
+
revue adversariale exécutée uniquement après PHASE 71
+
nouveaux findings adversariaux soumis aux mêmes preuves
+
Semgrep exécuté ou blocage précisément établi
+
rapport final honnête
```

# 63. RÈGLE FINALE ABSOLUE

Le workflow analysant reste :

```text
71 SKILLS
=
71 PHASES
=
1 SKILL PAR PHASE
```

Puis, **et seulement puis** :

```text
POST-RUN ADVERSARIAL REVIEW
```

Cette revue adversariale est l’unique exception.

Elle n’est :

```text
ni un skill
ni une phase
ni PHASE 72
```

Elle ne peut jamais s’insérer entre les phases.

Architecture finale :

```text
PHASE 01
↓
PHASE 02
↓
...
↓
PHASE 70
↓
PHASE 71
↓
FIN DU WORKFLOW 71 SKILLS
↓
REVUE ADVERSARIALE FINALE
↓
VALIDATION MÉCANIQUE DES ÉVENTUELS NOUVEAUX FINDINGS
↓
RAPPORT FINAL
```

Le but est de passer de :

```text
ORCHESTRATION SANS ANALYSE
```

à :

```text
ORCHESTRATION
+
ANALYSE RÉELLE
+
JSON STRICT
+
PREUVES FICHIER:LIGNE
+
CONSOLIDATION
+
REVUE ADVERSARIALE FINALE
+
RAPPORT HONNÊTE
```

**Ne change pas les 71 phases. Branche les moteurs, vérifie chaque preuve, termine PHASE 71, puis seulement exécute la revue adversariale finale.**

# REVUE ADVERSARIALE FINALE — PÉRIMÈTRE STRICTEMENT LIMITÉ AUX FINDINGS

La revue adversariale finale indépendante intervient uniquement après la terminaison de PHASE 71.

Elle n'est pas une phase supplémentaire.

Elle n'est pas PHASE 72.

Elle n'exécute aucun des 71 skills.

Elle ne recommence pas l'audit du projet.

Son périmètre est strictement limité aux findings produits par les 71 phases et conservés après les contrôles précédents.

Entrées autorisées :

- findings consolidés ;
- provenance de chaque finding ;
- phase(s) ayant produit le finding ;
- fichier concerné ;
- start_line ;
- end_line ;
- preuve source mécaniquement vérifiée ;
- sévérité ;
- confiance ;
- CWE éventuel ;
- reachability éventuelle ;
- limitations associées ;
- résultats de fp-check ;
- résultats de second-opinion.

Pour chaque finding, la revue adversariale doit chercher à répondre à :

1. La preuve soutient-elle réellement la conclusion de sécurité ?
2. Le finding est-il surinterprété ?
3. Existe-t-il une condition dans le code qui rend le finding impossible ?
4. La sévérité est-elle correctement évaluée ?
5. La confiance est-elle justifiée ?
6. Le finding est-il réellement différent d'un autre finding ?
7. Une information importante contredit-elle ce finding ?
8. Le finding doit-il être confirmé, contesté ou rejeté ?

Sorties autorisées :

CONFIRMED
DISPUTED
REJECTED
NEEDS_REVIEW

La revue adversariale ne doit PAS :

- rechercher de nouvelles vulnérabilités dans l'ensemble du projet ;
- lancer un nouvel audit du code ;
- parcourir librement la cible à la recherche de nouveaux findings ;
- créer une PHASE 72 ;
- modifier l'ordre ou les statuts historiques des 71 phases ;
- exécuter Semgrep, CodeQL ou un autre scanner ;
- relancer un skill ;
- ajouter un nouveau finding qui n'existait pas avant cette revue.

Elle peut relire uniquement les fichiers et lignes référencés par un finding lorsque cela est nécessaire pour vérifier ou contester ce finding.

Chaque décision adversariale doit conserver la provenance du finding original.

La revue adversariale est donc :

UNE CONTRE-EXPERTISE DES FINDINGS EXISTANTS

et non :

UN NOUVEL AUDIT DE LA CIBLE.
  
