---
name: stream-search
description: Find where movies and TV shows are available to stream, rent or buy (Netflix, HBO Max, Apple TV+, Disney+, Hulu, Prime Video, Peacock, Paramount+ and others), browse what a service carries by genre/year/mood, and check what's new or leaving a service this month. Use this skill whenever the user asks "where can I watch/stream X", "is X on Netflix", "what's on Max", "what should I watch on Apple TV", "what's leaving Netflix", "new on Hulu this month", or mentions a movie or show together with any streaming service, even casually and even if they don't say "stream". Also use it for anything about the Movie Picker web app: starting or opening it, "what should I watch tonight" or "recommend me something" (personalized from the user's Movie Picker profile), "what did the app recommend me", "what's my taste", or what they've marked as seen. Defaults to US availability.
---

# Stream Search

Help the user find out where a movie or show is available to watch, what a service carries, and what is arriving on or leaving a service. Streaming catalogs change every month, so anything you remember from training is probably stale. Always look it up, and treat memory only as a hint for what to look up.

**Data sources:**
- **TMDB API** via `scripts/tmdb.py` is the primary source for "where to watch" and "browse a service". It returns structured availability data that TMDB gets from JustWatch, so it's faster and more reliable than reading web pages.
- **Web search** covers what TMDB can't: new/leaving lists, prices, release schedules. It's also the fallback when the script fails.
- **The Movie Picker web app** via `scripts/movie_picker.py` holds the user's own profile: their services, taste learned from this-or-that quizzes, liked movies, seen movies and past recommendations. It also starts the app. See **Movie Picker** below.

## Defaults

- **Region: United States.** Use US availability unless the user names another country. If they do, pass `--region GB` (or the ISO code for that country) and say which region the results are for.
- **Date:** note today's date in the final source line.

## Using the TMDB script

Run it with `python3 <skill-dir>/scripts/tmdb.py <command>`. It prints JSON.

| Command | Use for |
|---|---|
| `where "<title>" [--year Y] [--type movie\|tv] [--seasons]` | Where a title streams. Returns the best match plus `other_matches` (same-name titles). |
| `providers <movie\|tv> <id> [--seasons]` | Availability for a specific TMDB id, e.g. after picking from `other_matches`. |
| `discover --service "hbo max" [--type movie\|tv] [--genre sci-fi] [--year-from Y] [--year-to Y] [--min-rating 7] [--sort popular\|rating\|newest] [--limit N]` | Browse a service's catalog. Several services: `--service "netflix,hulu"`. |
| `search "<title>"` / `services` / `genres --type tv` | Look up ids, exact service names, genre names. |

Service names are matched loosely ("max", "apple tv+", "prime" all work). If a name is ambiguous the script lists the candidates. Run `services` and pick the right one.

**If the script fails**, fall back to web search for that request and mention it briefly at the end ("TMDB wasn't available, so this comes from web search"). Ways it can fail:
- `"error": "No TMDB credentials..."`: tell the user once how to set up a key (see **Setup** below).
- A network error, or a result that's clearly wrong.

## Movie Picker (the companion web app)

Movie Picker is the web app in the same repo (`../movie-picker` from this skill). Users pick their streaming services, answer this-or-that questions, and get recommendations. Everything is saved per profile. Run the bridge script with `python3 <skill-dir>/scripts/movie_picker.py <command>`. It prints JSON.

| Command | Use for |
|---|---|
| `start [--port 8000]` | Start the app in the background if it isn't running, and get its URL. Safe to run when it's already up: it just returns the URL. |
| `status` / `stop` | Check whether it's running; stop one that `start` launched. |
| `profiles` | Every profile with their services and quiz / answer / seen counts. |
| `profile NAME` | One profile in full: `services`, `overall_taste`, `liked_in_quizzes`, `seen`, `recent_quizzes`, and `recommendations` (unseen first, newest first, each with match %, "why", and the services it was on). |

`start` needs the TMDB key. If it reports `TMDB_API_KEY is not set`, rerun as `source ~/.zshrc && python3 ...`. Reading profiles doesn't need the key or a running app. The script reads the database directly and can't change it.

**When the user wants to open or use the app** ("start the movie picker", "I want to take the quiz"): run `start`, then give them the URL as a link on its own line. Mention that the first quiz for a new mix of services takes a few seconds to load.

**When the user wants something personal** ("what should I watch tonight?", "recommend me a movie", "anything good for me on HBO Max?"):
1. Run `profiles`. If there's one profile, use it. If there are several and the user didn't say who they are, ask which one. If there are none, offer to `start` the app so they can take the quiz, and meanwhile answer with Mode 2.
2. Run `profile NAME` and work from it:
   - **Unseen recommendations first.** They're already matched to the user's taste. Recommendations can be days or weeks old, though, so check the top few with `tmdb.py where` before presenting them. Drop any that left the user's services, and say so briefly.
   - **Need more, or a different mood?** Run `tmdb.py discover` with the profile's `services` and genres that fit `overall_taste` and the request. Leave out everything in `seen`, `liked_in_quizzes` (already known to them) and the recommendations already listed.
   - **Never suggest anything in `seen`.** The user has told the app they've watched it.
3. Explain picks in the user's terms: "you liked Dune and Arrival in your quizzes", "your taste leans sci-fi and 2010s".
4. Answer in chat, then offer the app link for a fresh quiz if their mood has changed.

**Questions about their data** ("what did the app recommend me last time?", "what's my taste?", "what have I marked as seen?"): answer from `profile NAME`, citing quiz dates.

**Where-to-watch questions from a user with a profile**: after the normal Mode 1 answer, say whether the title is on one of *their* services ("You have HBO Max, so you can watch it now").

## Service names and aliases

Users use old and new names interchangeably. Use the current name in your answer:

- **HBO Max**: also "Max" (its name 2023–2025) and "HBO".
- **Apple TV+**: also "Apple TV" (Apple dropped the "+" in late 2025). Don't confuse the **subscription** with the **Apple TV store**, where titles are rented or bought one at a time. TMDB calls the subscription "Apple TV" and the store "Apple TV Store".
- **Prime Video**: "Amazon", "Prime". Be clear whether a title is *included with Prime*, available through a *paid channel add-on*, or *rent/buy only*. TMDB lists add-ons as separate providers such as "HBO Max Amazon Channel" or "Apple TV Amazon Channel". Present those as "add-on channel", not as included with Prime. If the same service also appears on its own, the add-on is just another way to pay for it, so mention it briefly at most.
- **Live TV and cable providers** (YouTube TV, Hulu + Live TV, Sling, fuboTV, Philo, DirecTV) and network apps that need a cable login (TBS, TNT, truTV, AMC, FX…) show up in TMDB's subscription list. They aren't what people mean by "streaming it on a service", so put them in a separate "With a live TV / cable subscription" line, or leave them out if a regular streaming service already has the title.
- Variants like "Netflix Standard with Ads", "Peacock Premium" or "Paramount Plus Essential" are tiers of the same service. Merge them into one service name and mention the tier only when it matters (e.g. the title is only on the ad tier).
- Free with ads: **Tubi**, **Pluto TV**, **The Roku Channel**, **Plex**, **Kanopy/Hoopla** (library card). Always mention these when present, since free is often what the user hopes to hear.

## Mode 1: Where to watch

1. Run `where "<title>"`, adding `--year` or `--type` if the user gave hints. For a TV show, add `--seasons`, because seasons are often split across services.
2. **Check that it picked the right title.** Look at `other_matches`. If there's a plausible different title with the same name (a remake, a different-country version, a movie vs. series), say which one you answered for (title, year, type) and offer the other. Re-run `providers` for the other one if the user's wording suggests it.
3. Sort the results into tiers for the answer:
   - **Included with subscription** (`subscription`)
   - **Free** (`free`, `free_with_ads`)
   - **Add-on channel**: any "… Channel" entries
   - **Live TV / cable**: see the note above
   - **Rent / buy** (`rent`, `buy`). List the stores briefly, and look up prices with web search only if the user asks.
4. For TV, use `status`, `next_episode` and `networks`. If a show is still airing, new episodes usually come out first on the original network or service. If `next_episode` is set, mention the date and look up the weekly schedule if useful.
5. If `streaming_anywhere` is false, say so plainly. Then search the web for an announced streaming date, or for a recent theatrical film, its expected digital release.
6. **Sanity check with the web when it matters.** TMDB/JustWatch data can lag by a few days. Do a quick web search to confirm when:
   - the title just came out or just moved services
   - the user says "I thought it was on X"
   - the result looks surprising

## Mode 2: Browse a service

1. Turn the request into filters (genre, decade, movie vs. series, minimum quality) and state any assumptions briefly. Don't interrogate the user.
2. Run `discover`. Good defaults:
   - "good" / "best": `--sort rating --min-votes 500`
   - "popular" / "trending": `--sort popular`
   - "new": `--sort newest`
   - `--limit 30`, so you have room to pick
3. Choose about 8–12 titles. Favor quality and a mix of well-known and lesser-known titles over the raw order. You can use your own knowledge of the films to write a one-line hook, but the list of what's available comes from TMDB.
4. If the request is fuzzy in a way genres can't capture ("feel-good", "mind-bending", "like Severance"), run `discover` with the closest genres and use your judgment to pick from the results. Or find candidates with web search and check each with `where`.

## Mode 3: New / leaving

TMDB doesn't track arrival or departure dates, so this mode uses web search.

1. Work out the time window. "This month" means the current calendar month, "this week" means the next 7 days, and "soon" means roughly the next 30 days.
2. Search for the service's official monthly announcement first (Netflix Tudum/newsroom, HBO Max press, Apple TV press, Disney+ press, Hulu press). Then search trusted roundups: `new on <service> <Month Year>`, `leaving <service> <Month Year>`, What's on Netflix, Decider, Tom's Guide, Vulture.
3. List titles **by date**, labeled as movie or series. For "leaving" lists, put the last day to watch up front, since that's what the user acts on.
4. Lists of what's leaving are often partial or published late (Netflix posts some only about two weeks ahead). Say when a list may be incomplete.
5. If the user follows up with "is X really leaving?" or "where will it go?", use `where` to see the current state.

## Answer format

Lead with the direct answer, then details. Keep it scannable. The example below shows the format only. Its facts are illustrative, not current data.

> **The Office** (US, 2005–2013, series) is streaming in the US on **Peacock**. All 9 seasons are included with a subscription.
>
> | How | Where |
> |---|---|
> | Subscription | Peacock |
> | Buy | Apple TV, Prime Video, Fandango at Home |
>
> *Streaming data from JustWatch via TMDB, checked Oct 21, 2026.*

For **browse** and **new/leaving**, give a short intro line, then a table or dated list.

End every answer with a one-line **source + date** note. When TMDB data was used, include "Streaming data from JustWatch via TMDB". TMDB's terms require that credit, and it also tells the user where the data came from. If sources conflicted or something couldn't be confirmed, say so in that line.

## Setup (tell the user only if the script reports missing credentials)

1. Create a free account at themoviedb.org, then go to **Settings → API** and request an API key (choose "Developer"; personal use is fine).
2. Add it to your shell profile, e.g. `echo 'export TMDB_API_KEY=your_key_here' >> ~/.zshrc`, then restart Claude Code.

Either the short v3 "API Key" or the long "API Read Access Token" works.

## Things to avoid

- Don't answer from memory without checking.
- In web search results, don't list a service just because a snippet mentions it next to the title ("unlike Netflix, Max has…"). Make sure the source actually says the title is available there.
- Don't link to or suggest unofficial or pirate streaming sites.
- Don't share one profile's data when another person asks. Profiles have no passwords, so go by who the user says they are.
