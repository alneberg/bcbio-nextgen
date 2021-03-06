"""Work with Broad's Java libraries from Python.

  Picard -- BAM manipulation and analysis library.
  GATK -- Next-generation sequence processing.
"""
import copy
import os
import subprocess
from contextlib import closing

from bcbio.broad import picardrun
from bcbio.pipeline import config_utils
from bcbio.provenance import do
from bcbio.utils import curdir_tmpdir

class BroadRunner:
    """Simplify running Broad commandline tools.
    """
    def __init__(self, picard_ref, gatk_dir, config):
        resources = config_utils.get_resources("gatk", config)
        self._jvm_opts = resources.get("jvm_opts", ["-Xms750m", "-Xmx2g"])
        self._picard_ref = config_utils.expand_path(picard_ref)
        self._gatk_dir = config_utils.expand_path(gatk_dir) or config_utils.expand_path(picard_ref)
        self._config = config
        self._resources = resources
        self._gatk_version = None

    def _context_jvm_opts(self, config):
        """Establish JVM opts, adjusting memory for the context if needed.

        This allows using less or more memory for highly parallel or multicore
        supporting processes, respectively.
        """
        jvm_opts = []
        memory_adjust = config["algorithm"].get("memory_adjust", {})
        for opt in self._jvm_opts:
            if opt.startswith(("-Xmx", "-Xms")):
                arg = opt[:4]
                modifier = opt[-1:]
                amount = int(opt[4:-1])
                if memory_adjust.get("direction") == "decrease":
                    amount = amount / memory_adjust.get("magnitude", 1)
                elif memory_adjust.get("direction") == "increase":
                    amount = amount * memory_adjust.get("magnitude", 1)
                opt = "{arg}{amount}{modifier}".format(arg=arg, amount=amount,
                                                       modifier=modifier)
            jvm_opts.append(opt)
        return jvm_opts

    def run_fn(self, name, *args, **kwds):
        """Run pre-built functionality that used Broad tools by name.

        See the gatkrun, picardrun module for available functions.
        """
        fn = None
        to_check = [picardrun]
        for ns in to_check:
            try:
                fn = getattr(ns, name)
                break
            except AttributeError:
                pass
        assert fn is not None, "Could not find function %s in %s" % (name, to_check)
        return fn(self, *args, **kwds)

    def cl_picard(self, command, options):
        """Prepare a Picard commandline.
        """
        options = ["%s=%s" % (x, y) for x, y in options]
        options.append("VALIDATION_STRINGENCY=SILENT")
        return self._get_picard_cmd(command) + options

    def run(self, command, options, pipe=False, get_stdout=False):
        """Run a Picard command with the provided option pairs.
        """
        cl = self.cl_picard(command, options)
        if pipe:
            subprocess.Popen(cl)
        elif get_stdout:
            p = subprocess.Popen(cl, stdout=subprocess.PIPE)
            stdout = p.stdout.read()
            p.wait()
            p.stdout.close()
            return stdout
        else:
            do.run(cl, "Picard {0}".format(command), None)

    def get_picard_version(self, command):
        if os.path.isdir(self._picard_ref):
            picard_jar = self._get_jar(command)
            cl = ["java", "-Xms5m", "-Xmx5m", "-jar", picard_jar]
        else:
            cl = [self._picard_ref, command]
        cl += ["--version"]
        p = subprocess.Popen(cl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        version = float(p.stdout.read().split("(")[0])
        p.wait()
        p.stdout.close()
        return version

    def cl_gatk(self, params, tmp_dir):
        support_nt = set()
        support_nct = set(["BaseRecalibrator"])
        gatk_jar = self._get_jar("GenomeAnalysisTK", ["GenomeAnalysisTKLite"])
        local_args = []
        cores = self._config["algorithm"].get("num_cores", 1)
        config = copy.deepcopy(self._config)
        if cores and int(cores) > 1:
            atype_index = params.index("-T") if params.count("-T") > 0 \
                          else params.index("--analysis_type")
            prog = params[atype_index + 1]
            if prog in support_nt:
                params.extend(["-nt", str(cores)])
            elif prog in support_nct:
                params.extend(["-nct", str(cores)])
                if config["algorithm"].get("memory_adjust") is None:
                    config["algorithm"]["memory_adjust"] = {"direction": "increase",
                                                            "magnitude": int(cores) // 2}
        if self.get_gatk_version() > "1.9":
            if len([x for x in params if x.startswith(("-U", "--unsafe"))]) == 0:
                params.extend(["-U", "LENIENT_VCF_PROCESSING"])
            params.extend(["--read_filter", "BadCigar", "--read_filter", "NotPrimaryAlignment"])
        local_args.append("-Djava.io.tmpdir=%s" % tmp_dir)
        return ["java"] + self._context_jvm_opts(config) + local_args + \
          ["-jar", gatk_jar] + [str(x) for x in params]

    def cl_mutect(self, params, tmp_dir):

        """Define parameters to run the mutect paired algorithm."""

        gatk_jar = self._get_jar("muTect")
        local_args = []
        cores = self._config["algorithm"].get("num_cores", 1)
        config = copy.deepcopy(self._config)

        local_args.append("-Djava.io.tmpdir=%s" % tmp_dir)
        return ["java"] + self._context_jvm_opts(config) + local_args + \
          ["-jar", gatk_jar] + [str(x) for x in params]

    def run_gatk(self, params, tmp_dir=None):
        with curdir_tmpdir() as local_tmp_dir:
            if tmp_dir is None:
                tmp_dir = local_tmp_dir
            cl = self.cl_gatk(params, tmp_dir)
            atype_index = cl.index("-T") if cl.count("-T") > 0 \
                          else cl.index("--analysis_type")
            prog = cl[atype_index + 1]
            do.run(cl, "GATK: {0}".format(prog), None)

    def run_mutect(self, params, tmp_dir=None):

        with curdir_tmpdir() as local_tmp_dir:
            if tmp_dir is None:
                tmp_dir = local_tmp_dir
            cl = self.cl_mutect(params, tmp_dir)
            prog = "MuTect"
            do.run(cl, "MuTect: {0}".format(prog), None)

    def get_gatk_version(self):
        """Retrieve GATK version, handling locally and config cached versions.
        Calling version can be expensive due to all the startup and shutdown
        of JVMs, so we prefer cached version information.
        """
        if self._gatk_version is not None:
            return self._gatk_version
        elif self._resources.get("version"):
            return self._resources["version"]
        else:
            gatk_jar = self._get_jar("GenomeAnalysisTK", ["GenomeAnalysisTKLite"])
            cl = ["java", "-Xms5m", "-Xmx5m", "-jar", gatk_jar, "-version"]
            with closing(subprocess.Popen(cl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout) as stdout:
                out = stdout.read().strip()
                # versions earlier than 2.4 do not have explicit version command,
                # parse from error output from GATK
                if out.find("ERROR") >= 0:
                    flag = "The Genome Analysis Toolkit (GATK)"
                    for line in out.split("\n"):
                        if line.startswith(flag):
                            version = line.split(flag)[-1].split(",")[0].strip()
                else:
                    version = out
            if version.startswith("v"):
                version = version[1:]
            self._gatk_version = version
            return version

    def gatk_type(self):
        """Retrieve type of GATK jar, allowing support for older GATK lite.
        Returns either `lite` (targeting GATK-lite 2.3.9) or `restricted`,
        the latest 2.4+ restricted version of GATK.
        """
        full_version = self.get_gatk_version()
        try:
            version, subversion, githash = full_version.split("-")
            if version.startswith("v"):
                version = version[1:]
        # version was not properly implemented in earlier versions
        except ValueError:
            version = 2.3
        if float(version) > 2.3:
            return "restricted"
        else:
            return "lite"

    def _get_picard_cmd(self, command):
        """Retrieve the base Picard command, handling both shell scripts and directory of jars.
        """
        if os.path.isdir(self._picard_ref):
            dist_file = self._get_jar(command)
            return ["java"] + self._jvm_opts + ["-jar", dist_file]
        else:
            # XXX Cannot currently set JVM opts with picard-tools script
            return [self._picard_ref, command]

    def _get_jar(self, command, alts=None):
        """Retrieve the jar for running the specified command.
        """
        dirs = []
        for bdir in [self._gatk_dir, self._picard_ref]:
            dirs.extend([bdir,
                         os.path.join(bdir, os.pardir, "gatk"),
                         os.path.join(bdir, "dist"),
                         os.path.join(bdir, "GATK"),
                         os.path.join(bdir, "GATK", "dist"),
                         os.path.join(bdir, "muTect"),
                         os.path.join(bdir, "MuTect"),
                         os.path.join(bdir, "muTect"),
                         os.path.join(bdir, "Picard-private", "dist")])
        if alts is None: alts = []
        for check_cmd in [command] + alts:
            for dir_check in dirs:
                check_file = os.path.join(dir_check, "%s.jar" % check_cmd)
                if os.path.exists(check_file):
                    return check_file
        raise ValueError("Could not find jar %s in %s:%s" % (command, self._picard_ref, self._gatk_dir))

def _get_picard_ref(config):
    """Handle retrieval of Picard for running, handling multiple cases:

    - A directory of jar files corresponding to individual commands.
    - The ubuntu/debian picard-tools commandline, which provides a shell wrapper around
      a shared jar.

    For a directory, configure resources with:
      picard:
        dir: /path/to/jars

    For the debian commandline, configure with:
      picard:
        cmd: picard-tools
    """
    try:
        picard = config_utils.get_program("picard", config, default="notfound")
    except config_utils.CmdNotFound:
        picard = "notfound"
    if picard == "notfound" or os.path.isdir(picard):
        picard = config_utils.get_program("picard", config, "dir")
    return picard

def runner_from_config(config, program="gatk"):
    return BroadRunner(_get_picard_ref(config),
                       config_utils.get_program(program, config, "dir"),
                       config)
