# Docker User Configuration

## Container User Settings

The Docker containers run as a non-root user for security. By default, containers create and use a user called `appuser` with UID 1000 and GID 1000.

### Environment Variables

Configure these in your `.env` file or docker-compose.yaml:

```bash
# Username inside the container (default: appuser)
APP_USER=appuser

# User ID (default: 1000)
APP_UID=1000

# Group ID (default: 1000)
APP_GID=1000
```

### Common Configurations

**Production VM (with admin user):**
```bash
APP_USER=admin
APP_UID=1000
APP_GID=1000
```

**Development (with app user, UID 1000):**
```bash
APP_USER=appuser
APP_UID=1000
APP_GID=1000
```

**Development (default - no configuration needed):**
```bash
# Leave empty or use defaults
# APP_USER=appuser
# APP_UID=1000
# APP_GID=1000
```

### Why This Matters

1. **File Ownership**: Files created by the container (logs, etc.) will be owned by the specified UID/GID
2. **Host Access**: Setting the UID/GID to match your host user allows you to access container-created files
3. **Security**: Running as non-root limits potential damage if the container is compromised

### Checking Your UID/GID

On Linux, run:
```bash
id
```

Example output:
```
uid=1000(appuser) gid=1000(appuser) groups=1000(appuser),27(sudo),...
```

Use the UID and GID values in your configuration.

### Troubleshooting

**Container keeps restarting:**
- Check if the specified user/UID conflicts with existing users in the container
- Try using the default `appuser` (no configuration needed)

**Permission denied on log files:**
- Make sure `APP_UID` and `APP_GID` match your host user
- Or set the appropriate user in the environment variables
