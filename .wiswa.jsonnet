local utils = import 'utils.libjsonnet';

{
  uses_user_defaults: true,
  description: 'Run Claude Code in a throwaway Docker container.',
  keywords: ['android', 'claude', 'docker', 'ghidra', 'reverse engineering', 'sandbox'],
  project_name: 'sbclaude',
  version: '0.0.1',
  // The host's claude binary is bind-mounted into a Linux container and run there, so the host
  // must supply an ELF one; every device and socket the run flags pass through is a Linux path;
  // and neither os.getuid nor syslog exists on Windows.
  supported_platforms: ['linux'],
  want_main: true,
  want_flatpak: true,
  publishing+: { flathub: 'sh.tat.sbclaude' },
  want_man: true,
  pyproject+: {
    project+: {
      classifiers+: ['Operating System :: POSIX :: Linux'],
    },
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
