# A box whose filesystem is saved on exit receives its environment in this file rather than through
# docker run -e. Login shells load it too, and `sbclaude shell` then sees what the session sees.
# Entries are NUL-separated, and a value may include a newline. Reading the NUL-separated format needs bash.
if [ -n "${BASH_VERSION:-}" ] && [ -r /run/sbclaude/env ]; then
    while IFS= read -r -d '' _sbclaude_kv; do
        export "${_sbclaude_kv?}"
    done < /run/sbclaude/env
    unset _sbclaude_kv
fi
