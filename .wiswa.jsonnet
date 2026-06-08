local utils = import 'utils.libjsonnet';

{
  uses_user_defaults: true,
  description: 'Run Claude Code in a throwaway Docker container with no sandbox and no permission prompts, against configurable host mounts, with an optional mobile reverse-engineering toolchain.',
  keywords: ['android', 'claude', 'docker', 'ghidra', 'reverse-engineering', 'sandbox'],
  project_name: 'sbclaude',
  version: '0.0.1',
  want_main: true,
  want_flatpak: true,
  publishing+: { flathub: 'sh.tat.sbclaude' },
  want_man: true,
  pyproject+: {
    tool+: {
      poetry+: {
        dependencies+: {
          docker: utils.latestPypiPackageVersionCaret('docker'),
          platformdirs: utils.latestPypiPackageVersionCaret('platformdirs'),
          tomlkit: utils.latestPypiPackageVersionCaret('tomlkit'),
        },
        group+: {
          dev+: {
            dependencies+: {
              'types-docker': utils.latestPypiPackageVersionCaret('types-docker'),
            },
          },
        },
      },
    },
  },
  hatch+: {
    build+: {
      targets+: {
        wheel+: {
          'force-include'+: {
            'sbclaude/docker': 'sbclaude/docker',
          },
        },
      },
    },
  },
}
