# GPT-5 Mini Data Analysis Project

This project performs data clustering using **GPT-5-mini** and a provided Excel dataset (`raw_data.xlsx`).

## 📁 Project Structure

```
.
├── .env.sample              # Example environment variables file
├── main.py   # Main analysis script
├── raw_data.xlsx            # Input dataset
├── requirements.txt         # Python dependencies
└── README.md                # Project documentation
```

---

### Clone the Repository

```bash
git clone https://github.com/dineshps13/Quantprocure.git
cd Quantprocure
```

---

### Create a Virtual Environment (Recommended)

**Mac / Linux**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows**

```bash
python -m venv venv
venv\Scripts\activate
```

---

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

### Set Up Environment Variables

1. Copy the sample environment file:

```bash
cp .env.sample .env
```

2. Open `.env` and add your OpenAI API key:

```
OPENAI_API_KEY=your_api_key_here
```

---

## Running the Analysis

Make sure `raw_data.xlsx` is in the project root directory.

Run the script:

```bash
python main.py
```

The script will:

* Load the Excel dataset
* Process the data
* Send relevant data to GPT-5-mini
* Output analysis results to the console (or file, depending on implementation)

---

## Requirements

* Python 3.8+
* OpenAI API key
* Internet connection (for API requests)

---

## Notes

* Ensure your API key has sufficient quota.
* Do not commit your `.env` file to version control.
* Large datasets may increase API usage costs.

---

## Troubleshooting

**Module not found errors**

```bash
pip install -r requirements.txt
```

**API key errors**

* Verify `.env` exists
* Confirm `OPENAI_API_KEY` is correctly set

---