# Prawdowid

> [!IMPORTANT]
> **MUST HAVE po konfiguracji bota Discord — link w przycisku Discorda to na razie placeholder.**
>
> W prawym dolnym rogu aplikacji jest przycisk z ikoną Discorda, który ma przenosić do bota Wróbel.
> Teraz wskazuje na `https://discord.com/`. Po skonfigurowaniu bota (instrukcja: `DiscordBot/README.md`):
>
> 1. W [src/app/core/links.ts](src/app/core/links.ts) podmień `DISCORD_BOT_URL` na link zaproszenia bota
>    (wypisuje go `./start.sh` albo `docker compose logs discord-bot | grep "Invite link"`)
>    lub na zaproszenie na Wasz serwer z botem.
> 2. Usuń komentarz `TODO(discord-link)` w tym pliku.
> 3. Usuń tę notkę z README.
>
> Komponent przycisku: [src/app/layout/discord-fab/discord-fab.ts](src/app/layout/discord-fab/discord-fab.ts).

This project was generated using [Angular CLI](https://github.com/angular/angular-cli) version 21.2.25.

## Development server

To start a local development server, run:

```bash
ng serve
```

Once the server is running, open your browser and navigate to `http://localhost:4200/`. The application will automatically reload whenever you modify any of the source files.

## Code scaffolding

Angular CLI includes powerful code scaffolding tools. To generate a new component, run:

```bash
ng generate component component-name
```

For a complete list of available schematics (such as `components`, `directives`, or `pipes`), run:

```bash
ng generate --help
```

## Building

To build the project run:

```bash
ng build
```

This will compile your project and store the build artifacts in the `dist/` directory. By default, the production build optimizes your application for performance and speed.

## Running unit tests

To execute unit tests with the [Vitest](https://vitest.dev/) test runner, use the following command:

```bash
ng test
```

## Running end-to-end tests

For end-to-end (e2e) testing, run:

```bash
ng e2e
```

Angular CLI does not come with an end-to-end testing framework by default. You can choose one that suits your needs.

## Additional Resources

For more information on using the Angular CLI, including detailed command references, visit the [Angular CLI Overview and Command Reference](https://angular.dev/tools/cli) page.
