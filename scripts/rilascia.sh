#!/usr/bin/env bash
# Pubblica una nuova versione dell'integrazione.
#
#   scripts/rilascia.sh 1.2.0
#
# Prima di lanciarlo aggiungi in CHANGELOG.md la sezione "## [X.Y.Z] - data".
# Lo script aggiorna la versione nel manifest, crea il commit e il tag vX.Y.Z e
# li invia a GitHub. Il push del tag avvia il workflow "Rilascio", che esegue
# tutti i controlli, crea la release con le note prese da CHANGELOG.md e
# allega plenitude.zip. Se un controllo fallisce la release non viene creata: corretto
# il problema, rilancia lo script con la stessa versione.
set -euo pipefail

errore() { echo "Errore: $*" >&2; exit 1; }

VERSIONE="${1:-}"
[[ "$VERSIONE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || errore "uso: $0 X.Y.Z (es. $0 1.2.0)"
TAG="v$VERSIONE"
MANIFEST="custom_components/plenitude/manifest.json"

cd "$(git rev-parse --show-toplevel)"
grep -q "^## \[$VERSIONE\]" CHANGELOG.md || errore "aggiungi prima la sezione [$VERSIONE] in CHANGELOG.md"
[[ "$(git branch --show-current)" == "main" ]] || errore "passa al branch main"
[[ -z "$(git status --porcelain)" ]] || errore "ci sono modifiche non salvate in un commit"
git pull --ff-only --quiet

# Un tag già pubblicato senza release (controlli falliti) può essere rifatto.
if git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  REPO=$(git remote get-url origin | sed -E 's#^(git@github.com:|https://github.com/)##; s#\.git$##')
  # la pagina di un tag esiste anche senza release: si interroga l'API delle release
  if curl -fsS -o /dev/null "https://api.github.com/repos/$REPO/releases/tags/$TAG"; then
    errore "la versione $TAG è già stata pubblicata"
  fi
  echo "Il tag $TAG esiste ma la release no: lo ricreo."
  git push --quiet origin ":refs/tags/$TAG"
fi
git tag -d "$TAG" >/dev/null 2>&1 || true

python3 - "$MANIFEST" "$VERSIONE" <<'PY'
import json, sys
percorso, versione = sys.argv[1], sys.argv[2]
with open(percorso, encoding="utf-8") as f:
    manifest = json.load(f)
manifest["version"] = versione
with open(percorso, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

if ! git diff --quiet -- "$MANIFEST"; then
  git commit --quiet -m "Versione $VERSIONE" -- "$MANIFEST"
fi
git tag -a "$TAG" -m "Versione $VERSIONE"
git push --quiet --atomic origin main "$TAG"

REPO_URL=$(git remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##')
echo
echo "Tag $TAG inviato: il workflow di rilascio è partito."
echo "Avanzamento: $REPO_URL/actions"
echo "Release:     $REPO_URL/releases/tag/$TAG (disponibile in pochi minuti)"
