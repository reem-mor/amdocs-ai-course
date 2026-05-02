# amdocs-ai-course

Course workspace for the Amdocs AI-Augmented Software Engineering program.

## Contents

- lectures
- homework
- projects
- notes
- resources

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Daily workflow

Start work:
```bash
cd C:\dev\amdocs-ai-course
.venv\Scripts\activate
git pull
```

End work:
```bash
git add .
git commit -m "your message"
git push
```

## Sync between laptops

This repository is used on both my personal laptop and Amdocs laptop.

- GitHub syncs the project files
- `requirements.txt` syncs Python dependencies
- each laptop has its own local `.venv`

## Notes

- Do not commit `.venv`
- Do not commit `__pycache__`
- Keep notebooks, homework, and notes organized by folder