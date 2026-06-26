"""
Tests for native container support (ContainerSpec / render_command) and the runner
integration that guarantees workflows run identically with and without a container.

The central invariant: a task command is a *shell snippet*. Whether it runs on the host
or inside a container, its shell operators (&&, pipes) and quoting must survive intact.
"""

import shlex

import pytest
from omegaconf import OmegaConf

import scriptflow as sf
from scriptflow.container import ContainerSpec, render_command


# --- ContainerSpec.wrap (takes a shell-snippet string) -----------------------

def test_wrap_basic():
    spec = ContainerSpec(image="img.sif")
    argv = spec.wrap("Rscript run.R")
    assert argv == ["apptainer", "exec", "img.sif", "bash", "-c", "Rscript run.R"]


def test_wrap_flags_and_runtime():
    spec = ContainerSpec(image="img.sif", runtime="singularity",
                         flags=["--cleanenv", "--no-home"])
    argv = spec.wrap("echo hi")
    assert argv[:4] == ["singularity", "exec", "--cleanenv", "--no-home"]
    assert argv[-3:] == ["bash", "-c", "echo hi"]


def test_wrap_binds_normalized():
    spec = ContainerSpec(image="img.sif", binds=["/data", "/a:/b"])
    argv = spec.wrap("true")
    # short form is expanded to src:dst; explicit src:dst is left untouched
    assert "/data:/data" in argv
    assert "/a:/b" in argv


def test_wrap_workdir_prefixes_cd():
    spec = ContainerSpec(image="img.sif", workdir="/proj root")
    inner = spec.wrap("ls")[-1]
    assert inner == "cd '/proj root' && ls"


def test_wrap_preserves_snippet_operators_unchanged():
    # The snippet is the final argv element, carried verbatim -- operators intact.
    spec = ContainerSpec(image="img.sif")
    inner = spec.wrap("cd 401k && Rscript run.R")[-1]
    assert inner == "cd 401k && Rscript run.R"


# --- the quoting round-trip (regression: the cd "too many arguments" bug) -----

def test_render_snippet_with_operators_round_trips():
    # cd ... && Rscript ... must come back out of the rendered line as one bash -c
    # token with its && acting as a real operator, not a quoted literal.
    t = sf.Task(cmd="cd 401k && Rscript --vanilla run.R /data x")
    spec = ContainerSpec(image="img.sif", workdir="/proj")
    line = render_command(t, spec)

    tokens = shlex.split(line)
    # the whole snippet is a single bash -c payload
    assert tokens[:5] == ["apptainer", "exec", "img.sif", "bash", "-c"]
    assert tokens[5] == "cd /proj && cd 401k && Rscript --vanilla run.R /data x"
    # and there is no stray, separately-quoted '&&' token (the original bug)
    assert "&&" not in tokens[6:]


def test_render_snippet_with_quoted_args_round_trips():
    t = sf.Task(cmd="Rscript run.R --label 'a b'")
    spec = ContainerSpec(image="img.sif")
    line = render_command(t, spec)
    payload = shlex.split(line)[-1]
    assert payload == "Rscript run.R --label 'a b'"


# --- render_command host vs container ----------------------------------------

def test_render_no_container_returns_snippet():
    t = sf.Task(cmd="cd 401k && Rscript run.R")
    assert render_command(t, None) == "cd 401k && Rscript run.R"


def test_render_task_opt_out_runs_on_host():
    t = sf.Task(cmd="apptainer build x.sif x.def", container=False)
    spec = ContainerSpec(image="img.sif")
    assert render_command(t, spec) == "apptainer build x.sif x.def"


def test_list_command_is_joined_into_snippet():
    # a genuine argv (list) becomes a shell-safe snippet
    t = sf.Task(cmd=["Rscript", "run.R", "a b"])
    assert t.get_command_str() == "Rscript run.R 'a b'"


# --- ContainerSpec.from_conf -------------------------------------------------

def test_from_conf_none():
    assert ContainerSpec.from_conf(None) is None
    assert ContainerSpec.from_conf({}) is None


def test_from_conf_requires_image():
    with pytest.raises(ValueError):
        ContainerSpec.from_conf({"runtime": "apptainer"})


def test_from_conf_from_omegaconf():
    conf = OmegaConf.create({
        "image": "img.sif", "binds": ["/data"],
        "flags": ["--cleanenv"], "workdir": "/proj",
    })
    spec = ContainerSpec.from_conf(conf)
    assert (spec.image, spec.binds, spec.flags, spec.workdir) == (
        "img.sif", ["/data"], ["--cleanenv"], "/proj")


# --- runner integration ------------------------------------------------------

def _slurm(**over):
    conf = {'maxsize': 5, 'user': 'u', 'account': 'a', 'partition': 'p',
            'modules': '', 'walltime': '00:01:00'}
    conf.update(over)
    return sf.HpcRunner_slurm(conf)


def test_slurm_script_wraps_and_preserves_operators():
    runner = _slurm(container={'image': 'img.sif', 'flags': ['--cleanenv'],
                               'binds': ['/data'], 'workdir': '/proj'})
    script = runner.build_script(sf.Task(cmd="cd 401k && Rscript run.R", name="t1"))
    assert "apptainer exec --cleanenv --bind /data:/data img.sif bash -c" in script
    # the entire snippet (with workdir cd and the && operator) is one quoted token
    assert "'cd /proj && cd 401k && Rscript run.R'" in script


def test_slurm_script_no_container_runs_on_host():
    runner = _slurm()
    script = runner.build_script(sf.Task(cmd="cd 401k && Rscript run.R", name="t1"))
    assert "apptainer" not in script
    assert "cd 401k && Rscript run.R" in script


def test_slurm_empty_modules_no_module_load():
    script = _slurm(modules='').build_script(sf.Task(cmd="echo hi", name="t1"))
    assert "module load" not in script


def test_slurm_modules_emitted():
    script = _slurm(modules='apptainer').build_script(sf.Task(cmd="echo hi", name="t1"))
    assert "module load apptainer" in script


def test_pbs_script_wraps_command():
    runner = sf.HpcRunner({'maxsize': 5, 'user': 'u', 'modules': '',
                           'walltime': '00:01:00', 'container': {'image': 'img.sif'}})
    script = runner.build_script(sf.Task(cmd="cd 401k && Rscript run.R", name="t1"))
    assert "apptainer exec img.sif bash -c 'cd 401k && Rscript run.R'" in script


def test_local_runner_wraps_via_bash(monkeypatch):
    import scriptflow.runners as runners
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured['argv'] = argv
        def poll(self):
            return None

    monkeypatch.setattr(runners.subprocess, "Popen", FakePopen)

    runner = sf.CommandRunner({'maxsize': 2, 'container': {'image': 'img.sif'}})
    runner.add(sf.Task(cmd="cd 401k && Rscript run.R", name="t1"))
    # local runner always goes through bash -c; the payload is the apptainer invocation
    assert captured['argv'][:2] == ["bash", "-c"]
    assert "apptainer exec img.sif bash -c 'cd 401k && Rscript run.R'" in captured['argv'][2]
