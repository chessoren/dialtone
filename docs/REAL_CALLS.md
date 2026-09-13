# Real-call plan (8 calls)

The simulated sets measure the detector. These eight calls measure it on real telephony: real TTS, codecs, STT and turn-taking, which is what the timing features actually see.

**Budget:** 8 of CALL-E's 20 free calls, and roughly 10 minutes of Vapi time (well within the $10 signup credit).

**What gets committed:**
- **Only derived results** go in `data/real/results/`: features, detection, verdict, timings and masked numbers.
- **Raw CALL-E responses** (with transcripts) stay in the git-ignored `data/real/raw/`.

**Setup (once):**

```bash
dialtone line setup            # creates 4 Vapi assistants + 1 free US number
```

**Environment variables** used in the commands below:
- `AI` is the free Vapi number.
- `ME` is the consenting human's phone.

**Goal** used for every call: `Book a table for two this Friday at 7 PM under the name Jordan Lee`.

| # | name | counterpart (truth) | line persona | CALL-E task mode | what it tests |
|---|---|---|---|---|---|
| 1 | `to-human` | human | none (consenting person's phone) | dialtone | Human ignores the token; stays in HUMAN mode; human-attested |
| 2 | `to-human-plain` | human | none | plain | Human baseline without the protocol |
| 3 | `to-dialtone-bot` | agent | `dialtone` | dialtone | Handshake + nonce; MACHINE mode; agent-asserted with code |
| 4 | `baseline-plain-bot` | agent | `plain` | plain | Duration baseline for the speed claim |
| 5 | `dialtone-to-plain-bot` | agent | `plain` | dialtone | Uncooperative bot: behavioural detection only |
| 6 | `to-yesbot` | agent | `yesbot` | dialtone | Two agents "confirm"; gate must refuse |
| 7 | `to-ivr` | ivr (emulated on Vapi) | `ivr` | dialtone | Menu detection. Labelled emulated, not a real carrier IVR |
| 8 | `to-dialtone-bot-2` | agent | `dialtone` | dialtone | Repeatability of handshake timing |

Each call follows the same pattern: switch the line persona, then place the call.

```bash
dialtone line use dialtone
dialtone call --to $AI --goal "Book a table for two this Friday at 7 PM under the name Jordan Lee" \
  --principal "Jordan Lee" --mode dialtone --name to-dialtone-bot --label agent --live
```

**After all calls:**

```bash
dialtone eval                                    # adds the real calls to eval/REPORT.md
python -c "from dialtone import demo; demo.build('real', out=__import__('pathlib').Path('web/data/demo.real.json'))"
```

For each call on the Vapi line, run `dialtone line inbound <vapi-call-id>` to show the inbound side classifying its caller.
