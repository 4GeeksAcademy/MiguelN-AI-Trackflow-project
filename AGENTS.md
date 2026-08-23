# AGENTS

## Memory-Bank Reading Order (Mandatory)

Before starting any task, read the markdown files in `./memory-bank` in this exact order:

1. `context.md`
2. `projectbrief.md`
3. `techContext.md`
4. `progress.md`

## Protected Files and Folders (Do Not Modify Without Explicit Developer Confirmation)

Before modifying any of the following files/folders, obtain explicit confirmation from the developer:

- `./CONTEXT.md`
- `./functionalities.md`

## Mandatory Workflow Before Any Commit

Complete these steps in order before creating any commit in this repository:

1. **Formatting (auto-fix style)**
   - Run formatting with auto-fix enabled so the codebase remains clean and consistent.

2. **Typechecking**
   - Run typechecking to verify all types compile.
   - If there are type errors, show them to the user and ask for confirmation before applying fixes.

3. **Tests**
   - Run the test suite to validate behavior and logic before committing.
   - If tests fail, show the failures to the user and ask for confirmation before applying fixes.

4. **Update `./memory-bank/progress.md`**
   - Add a summary of the work completed on the present date (include the date explicitly).
   - Document goals accomplished.
   - Document future goals and/or goals still missing to be accomplished.
