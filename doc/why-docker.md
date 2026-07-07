# Why Docker?

There are several reasons:

## Consistency of environment

When you run our Docker containers, the runtime environment is the same on every installation. You get a well-tested, repeatable base for your server.

## Convenience

Docker Compose builds a complex stack from a single `docker-compose.yml`. To back up the system, save that file plus your RYSEN configuration under `/etc/rysen/` — enough to recreate the whole server.

## Security

Each component runs in a compartmentalised container, which limits exposure if one service is compromised.

## Reduced support overhead

Past issues have traced to OS or Python version differences on bare-metal installs. Docker gives a consistent environment. Compartmentalisation also simplifies port and process management — what's in the container stays in the container.

See [install.md](install.md) for the recommended install path.

*Credits: Simon G7RZU (original rationale); Shane Daley M0VUB aka ShaYmez (RYSEN Docker stack).*
