# TEAM.md - Software engineering team

I am part of a team alongside other agents as well as humans. Together we develop software.

## Tools

We use Github to work on source code and to track issues. There should be a project board or some other mechanism to track whether an issue is in progress, ready for review etc. We use issues to assign tasks and discuss them. Any discussion about the task itself goes into the issue as a comment so it is persisted for later viewing. General discussion can also happen in the agent or web_ui channels.

## Team

Our team consists of

- A human team lead. They provide direction and final reviews.
- An agent responsible for architecture. They maintain a bird's-eye view of the project and how it is structured. They provide advice on architectural questions to the rest of the team and review code for their long-term impact.
- A software engineering agent. They are the main force modifying code.
- A reviewer agent. They review any code before it is merged, providing a fresh set of eyes unbiased by the thoughts that drove the implementation.

## Workflow

This is a typical workflow for a task like a new feature.

1. **Assignment**: An issue moves to "in progress" and is assigned to the software engineering agent.
2. **Clarification Phase**: They read the issue carefully.
   - For ambiguity on behavior/requirements: Comment on issue, clarifying with the creator of the issue (usually the team lead). Discuss what's intended and what's possible.
   - If the implementation has an impact on the project's architecture, also consider involving the architect agent in planning.
3. **Implementation & PR**:
   - Work on a dedicated feature branch using.
   - Ensure all local tests pass and self-review diffs before opening a PR.
   - Create a pull request.
   - Request reviews from the reviewer and architect agents.
4. **Review Cycle**:
   - Reviewer agent checks diffs for code hygiene, efficiency, and test coverage.
   - Architect agent reviews how the changes affect the project structure and updates documentation/ADRs if necessary.
   - If changes are requested, collaborate to resolve. Discuss, address feedback, push changes and re-request a review.
   - If it looks like no agreement can be reached, involve the human team lead early.
5. **Final Gate**:
   - Once agent reviewers are satisfied, ask human team lead for a final review. They will handle the merge.

## Communication standards

- Use concise, precise technical language in issue comments and PR reviews.
- When requesting changes on PRs, cite specific line numbers, supply concrete suggestions, and explain _why_.
