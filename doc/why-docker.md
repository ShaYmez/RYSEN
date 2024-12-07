# Why Docker?

There are several reasons:

## Consistency of environment
When you run our docker containers, the runtime environment is the same over every installation. This means you are working with a well-tested, repeatable, stable base for your server.

## Convenience
It's a lot easier in the long run to use our provided Docker images. Using Docker and Docker Compose you can build a complex infrastructure by building one text file (docker-compose.yml). When it comes to backing up the system, all you need to do is backup this one file and the freedmr configuration files and that's enough to completely re-create the whole server.

## Security 
Each server component is compartmentalised and runs in read0only container which makes it harder to compromise and limits what an atacker can do if they do get in.

## Reduced support overhead
We have in the past had to debug issues which turned out to be OS version or Python version related. If you use docker, support is much easier as the environment os consistent - see above.
Compartmentalisation - many of the components we use have several parts and use several network ports. Using docker, you don't have to worry about managing any of that. What's in the container, stays in the container.

*Credits*
Simon G7RZU