# Installation

> Professional installation guide for **sr-search-replace** across major platforms.

## Supported Environments

- Linux (Debian/Ubuntu, RHEL/CentOS, Fedora, Arch, Alpine)
- macOS (Intel & Apple Silicon)
- BSD (FreeBSD, OpenBSD)
- Windows (via WSL2)
- Containers (Docker, Kubernetes)

### Quick Binary Install (Universal)

```bash
curl -fsSL https://github.com/paulmann/sr-search-replace/releases/download/v6.1.0/sr -o sr
chmod +x sr
sudo mv sr /usr/local/bin/sr

sr --version
```

This approach works on any UNIX-like system with curl and a POSIX shell.
