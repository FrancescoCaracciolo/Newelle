import re 
import html
import xml 
import xml.dom.minidom
import json


def quote_string(s):
    if "'" in s:
        return "'" + s.replace("'", "'\\''") + "'"
    else:
        return "'" + s + "'"

def markwon_to_pango(markdown_text):
    markdown_text = html.escape(markdown_text)
    initial_string = markdown_text
    # Convert bold text
    markdown_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', markdown_text)
    
    # Convert italic text
    markdown_text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', markdown_text)

    # Convert monospace text
    markdown_text = re.sub(r'`(.*?)`', r'<tt>\1</tt>', markdown_text)

    # Convert strikethrough text
    markdown_text = re.sub(r'~(.*?)~', r'<span strikethrough="true">\1</span>', markdown_text)
    
    # Convert links
    markdown_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', markdown_text)
    
    # Convert headers
    absolute_sizes = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large']
    markdown_text = re.sub(r'^(#+) (.*)$', lambda match: f'<span font_weight="bold" font_size="{absolute_sizes[6 - len(match.group(1))]}">{match.group(2)}</span>', markdown_text, flags=re.MULTILINE)
    
    # Check if the generated text is valid. If not just print it unformatted
    try:
        xml.dom.minidom.parseString("<html>" + markdown_text + "</html>")
    except Exception as e:
        print(markdown_text)
        print(e)
        return initial_string
    return markdown_text

def human_readable_size(size: float, decimal_places:int =2) -> str:
    size = int(size)
    unit = ''
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def extract_json(input_string: str) -> str:
    """Extract JSON string from input string

    Args:
        input_string (): The input string 

    Returns:
        str: The JSON string 
    """
    # Regular expression to find JSON objects or arrays
    json_pattern = re.compile(r'\{.*?\}|\[.*?\]', re.DOTALL)
    
    # Find all JSON-like substrings
    matches = json_pattern.findall(input_string) 
    # Parse each match and return the first valid JSON
    for match in matches:
        try:
            json_data = json.loads(match)
            return match
        except json.JSONDecodeError:
            continue
    print("Wrong JSON", input_string)
    return "{}"


def remove_markdown(text: str) -> str:
    """
    Remove markdown from text

    Args:
        text: The text to remove markdown from 

    Returns:
        str: The text without markdown 
    """
    # Remove headers
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    
    # Remove emphasis (bold and italic)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
    text = re.sub(r'__(.*?)__', r'\1', text)          # Bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)        # Italic
    text = re.sub(r'_(.*?)_', r'\1', text)            # Italic
    
    # Remove inline code
    text = re.sub(r'`([^`]*)`', r'\1', text)
    
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)

    # Remove links, keep the link text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # Remove images, keep the alt text
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'\1', text)

    # Remove strikethrough
    text = re.sub(r'~~(.*?)~~', r'\1', text)

    # Remove blockquotes
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)

    # Remove unordered list markers
    text = re.sub(r'^\s*[-+*]\s+', '', text, flags=re.MULTILINE)

    # Remove ordered list markers
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # Remove extra newlines
    text = re.sub(r'\n{2,}', '\n', text)

    return text.strip()

def convert_think_codeblocks(text: str) -> str:
    """Convert think codeblocks to markdown

    Args:
        text (str): The text to convert 

    Returns:
        str: The converted text 
    """
    return text.replace("<think>", "```think").replace("</think>", "```")

def remove_thinking_blocks(text):
  """
  Removes <think>...</think> blocks from a given text using regular expressions.

  Args:
    text: The input text string.

  Returns:
    The text string with all <think>...</think> blocks removed.
  """
  pattern = r"<think>.*?</think>"  # Non-greedy match
  cleaned_text = re.sub(pattern, "", text, flags=re.DOTALL) # flags=re.DOTALL allows . to match newline characters
  return cleaned_text

def get_edited_messages(history: list, old_history: list) -> list | None:
    """Get the edited messages from the history

    Args:
        history (list): The history
        prompts (list): The prompts

    Returns:
        list: The edited messages IDs, or None if there are removed messages
    """
    if len(history) != len(old_history):
        return None
    edited_messages = []
    for i in range(len(history)):
        if history[i] != old_history[i]:
            edited_messages.append(i)
    return edited_messages


def add_S_to_sudo(commands_string):
    """
    Adds the -S flag to every sudo command in a string of Linux commands.

    Args:
        commands_string: A string containing Linux commands.

    Returns:
        A string with the -S flag added to all sudo commands.
    """

    def replace_sudo(match):
        command_parts = match.group(0).split()
        if "-S" in command_parts:
            return " ".join(command_parts)  # Already has -S
        
        sudo_index = command_parts.index("sudo")

        if len(command_parts) > sudo_index + 1 and command_parts[sudo_index + 1].startswith("-"):
            #sudo has options, insert -S
            command_parts.insert(sudo_index + 1, "-S")
            return " ".join(command_parts)

        elif len(command_parts) > sudo_index:
            #Insert -S after sudo, no existing option
            command_parts.insert(sudo_index + 1, "-S")
            return " ".join(command_parts)
        else:
            # this case should not happen in normal command string
            return " ".join(command_parts)


    # Use regex to find all "sudo" commands, handling different scenarios
    modified_string = re.sub(r'(^|\s)sudo(\s+[\w\/\.-]+)*', replace_sudo, commands_string)

    return modified_string

def rgb_to_hex(r, g, b):
    """
    Convert RGB values from float to hex.

    Args:
        r (float): Red value between 0 and 1.
        g (float): Green value between 0 and 1.
        b (float): Blue value between 0 and 1.

    Returns:
        str: Hex representation of the RGB values.
    """
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def extract_expressions(text, expressions_list):
    expressions = []
    current_expression = None
    current_text = ""

    tokens = text.split()
    i = 0
    while i < len(tokens):
        tokens[i] = tokens[i].rstrip(".").rstrip("?").rstrip("!").rstrip(",")
        if tokens[i].startswith("(") and tokens[i].endswith(")"):
            expression = tokens[i][1:-1]
            if expression in [exp.replace("_", "") for exp in expressions_list]:
                if expression not in expressions:
                    expression = expressions_list[[exp.replace("_", "") for exp in expressions_list].index(expression)]
                if current_text.strip():
                    expressions.append({"expression": current_expression, "text": current_text.strip()})
                    current_text = ""
                current_expression = expression
            else:
                current_text += tokens[i] + " "
        else:
            if current_expression is None:
                current_text += tokens[i] + " "
            else:
                current_text += tokens[i] + " "
        i += 1

    if current_expression:
        expressions.append({"expression": current_expression, "text": current_text.strip()})
    else:
        expressions.append({"expression": None, "text": current_text.strip()})

    return expressions
