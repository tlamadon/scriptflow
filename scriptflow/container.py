"""
Container support for scriptflow runners.

A `ContainerSpec` knows only how to wrap a single shell command into a container-runtime
invocation, e.g. `apptainer exec ... bash -c '<snippet>'`. Containers are chosen
**per task**: a task carries its own `ContainerSpec` (via `container=` on `Task`) or none,
in which case it runs on the host. There is no executor-level default container. A
`ContainerSpec` does not decide *whether* to wrap (that follows from whether the task has
one) and it does not load the runtime binary (that is a host concern handled by executor
`modules`/`setup`).

Key design point: a task command is treated as a *shell snippet* (it may contain `&&`,
pipes, redirects, quoted arguments), never as an argv to be tokenized. The snippet is
carried verbatim as the single `bash -c` payload, so a later `shlex.join` (HPC script) or
argv hand-off (local runner) quotes it as one token and its operators/quotes survive
intact. Tokenizing-then-rejoining a snippet cannot preserve both operators and quoting,
which is the bug this design avoids.
"""

import shlex

from omegaconf import OmegaConf


class ContainerSpec:

    def __init__(self, image, runtime="apptainer", binds=None, flags=None, workdir="",
                 env=None):
        self.image = image
        self.runtime = runtime
        self.binds = list(binds) if binds else []
        self.flags = list(flags) if flags else []
        self.workdir = workdir
        self.env = dict(env) if env else {}

    @classmethod
    def from_conf(cls, conf):
        """Build a spec from a `container=` dict (per-task), or None if empty.

        Accepts a plain dict or an OmegaConf node. Raises if the block is present but
        does not specify an `image`.
        """
        if not conf:
            return None
        if OmegaConf.is_config(conf):
            conf = OmegaConf.to_container(conf, resolve=True)
        image = conf.get("image")
        if not image:
            raise ValueError("container config requires an 'image'")
        return cls(
            image=image,
            runtime=conf.get("runtime", "apptainer"),
            binds=conf.get("binds", []),
            flags=conf.get("flags", []),
            workdir=conf.get("workdir", ""),
            env=conf.get("env", {}),
        )

    def wrap(self, cmd):
        """Wrap a shell snippet (string) into a runtime-exec argv list.

        The snippet is the final argv element (the `bash -c` payload); it is never
        re-tokenized, so shell operators and quoting inside it are preserved.
        """
        inner = f"cd {shlex.quote(self.workdir)} && {cmd}" if self.workdir else cmd
        argv = [self.runtime, "exec", *self.flags]
        for b in self.binds:
            argv += ["--bind", b if ":" in b else f"{b}:{b}"]
        for k, v in self.env.items():
            argv += ["--env", f"{k}={v}"]
        argv += [self.image, "bash", "-c", inner]
        return argv


def render_command(task):
    """Resolve a task into a single shell command line for execution.

    - host (task has no container): the task's shell snippet, verbatim.
    - container (task carries a `ContainerSpec`): an
      `apptainer exec ... bash -c '<snippet>'` line, with the snippet quoted as a single
      token so its operators and quoting survive.

    The returned string is dropped straight into an HPC job script, or handed to
    `bash -c` by the local runner.
    """
    cmd = task.get_command_str()
    spec = getattr(task, "container_spec", None)
    return shlex.join(spec.wrap(cmd)) if spec is not None else cmd
