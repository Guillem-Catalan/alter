#!/usr/bin/env bash
# Download the whisper.cpp speech model used for voice notes (~570 MB, multilingual).
# Usage: scripts/download-whisper-model.sh [model-name]   e.g. ggml-small.bin for a lighter one
set -euo pipefail
cd "$(dirname "$0")/.."
name=${1:-ggml-large-v3-turbo-q5_0.bin}
# Same folder the app uses: DATA_DIR, or the per-user app folder (agent/config.py: default_data_dir).
data=${DATA_DIR:-$(python3 -c 'from agent.config import default_data_dir; print(default_data_dir())')}
dir=$data/models
mkdir -p "$dir"
chmod 700 "$data"
if [[ -s $dir/$name ]]; then
  echo "Already there: $dir/$name"
  exit 0
fi
curl -fL --retry 3 -o "$dir/$name.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$name"
mv "$dir/$name.part" "$dir/$name"
echo "Saved $dir/$name"
[[ $name == ggml-large-v3-turbo-q5_0.bin ]] || echo "Set WHISPER_MODEL=$dir/$name in .env"
