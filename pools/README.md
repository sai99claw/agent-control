# pools/

One ticket pool per project, each its own git repository (D-079: the pool is a sub repo of
agent-control). `python3 loop/tickets.py init` creates `pools/<project>` as a local repository.
Once it has a remote, register it as a submodule:

    git submodule add <pool remote url> pools/<project>

Until then the directory is ignored by agent-control's own git (see .gitignore). A write to a pool
counts only once its push succeeds; a pool without a remote counts a commit as the write.
