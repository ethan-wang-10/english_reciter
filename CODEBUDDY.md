# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## Project Overview

This is an intelligent English vocabulary learning system (英语背诵系统) that implements spaced repetition learning algorithms based on the Ebbinghaus forgetting curve. The system provides interactive word review sessions with text-to-speech capabilities and tracks learning progress.

## Common Commands

### Running the Application
```bash
python3 reciter.py
```

### Running Tests
```bash
python3 test_stats.py
```

### Installing Dependencies
The project requires the following Python packages:
```bash
pip install gtts playsound prettytable tencentcloud-hunyuan readchar requests
```

### Data Files
- `learning_data.json`: Main data file storing word progress (auto-created if missing)
- `words.txt`: Source word list for importing
- `word_examples.json`: Local example sentence database (optional)

## Architecture

### Core Components

**Config Class** (reciter.py:16-25)
- Central configuration management
- `MAX_SUCCESS_COUNT`: Words require 8 successful reviews to master
- `REVIEW_INTERVAL_DAYS`: Ebbinghaus-based intervals [1, 2, 4, 7, 15, 30, 60, 90]
- `TTS_ENABLED`: Toggle text-to-speech functionality

**HunyuanGenerator Class** (reciter.py:28-105)
- Optional AI-powered example sentence generation using Tencent Hunyuan API
- Falls back to local example database or default sentences
- Splits bilingual responses (English_Chinese format)

**Word Class** (reciter.py:108-136)
- Data model for vocabulary words
- Tracks: `success_count`, `review_round`, `review_count`, `next_review_date`
- Provides JSON serialization/deserialization
- Backwards compatible with older data formats

**WordReciter Class** (reciter.py:139-576)
- Main learning engine implementing spaced repetition logic
- Key methods:
  - `daily_review()`: Orchestrates today's review session (reciter.py:415-489)
  - `_get_today_review_list()`: Selects words due for review using round-based logic (reciter.py:226-252)
  - `_practice_word()`: Interactive spelling practice with TTS (reciter.py:345-413)
  - `show_status()`: Displays learning progress dashboard (reciter.py:254-291)
  - `_check_and_advance_round()`: Manages review round progression (reciter.py:491-516)
- Data persistence: `_load_data()` and `_save_data()`

**ReciterCLI Class** (reciter.py:580-622)
- Command-line interface providing menu-driven navigation
- Options: Start review, View progress, Import words, View mastered words, Review mastered words

### Learning Algorithm

**Spaced Repetition Logic**
- Words progress through 8 review rounds based on success count
- Correct answer: `success_count + 1`, set next review date using Ebbinghaus intervals
- Wrong answer: Reset progress (no `success_count` change, maintain `review_count`)
- Mastery: `success_count >= 8` moves word to mastered list
- Review intervals: 1, 2, 4, 7, 15, 30, 60, 90 days

**Round-Based Review System**
- Words grouped by `review_round` (current round priority)
- Within each round, words sorted by `review_count` (fewer reviews first)
- Ensures fair coverage and prevents neglect

**Example Sentence Hierarchy**
1. Cached word example (if available)
2. NLTK WordNet corpus examples
3. Tencent Hunyuan AI generation (if credentials provided)
4. Local example database (`word_examples.json`)
5. Default generated sentence

### Data Structure

**learning_data.json Format**
```json
{
  "all_words": [
    {
      "english": "word",
      "chinese": "meaning",
      "success_count": 0,
      "next_review_date": "2026-01-31",
      "example": "sentence_translation",
      "review_round": 0,
      "review_count": 0
    }
  ],
  "mastered_words": [...]
}
```

### Key Implementation Details

**Text-to-Speech** (reciter.py:323-343)
- Uses macOS `say` command for English pronunciation
- Extracts English portion from bilingual sentences (English_Chinese)
- Gracefully handles TTS failures

**Interactive Input** (reciter.py:382-396)
- Uses `readchar` library for character-by-character input
- Real-time character count display
- Supports special commands: 'h' (show answer), 's' (play audio)

**Data Compatibility**
- Handles missing `review_round` and `review_count` fields for legacy data
- Auto-creates data file with default values if corrupted/missing

## Development Notes

- The system is designed for Chinese speakers learning English vocabulary
- All user-facing text is in Chinese with English examples
- The review algorithm balances between new words and reinforcing learned vocabulary
- Progress is saved after each word review to prevent data loss
- Overdue words have their `next_review_date` adjusted to today on startup
