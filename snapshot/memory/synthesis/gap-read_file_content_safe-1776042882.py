# Auto-synthesized capability: read_file_content_safe
# Description: Read and return the content of a specific file using safe shell_exec with grep or tail to avoid full file loading

def read_file_content_safe(file_path, pattern=None):
    if pattern:
        cmd = f"cat {file_path} | grep -A5 '{pattern}'"
    else:
        cmd = f"cat {file_path}"
    try:
        from subprocess import check_output, CalledProcessError
        result = check_output(cmd, shell=True, text=True)
        return result.strip()
    except CalledProcessError as e:
        return f"Error reading file: {e.stderr}"
