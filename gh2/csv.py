from __future__ import absolute_import

import argparse
import collections
import datetime
import csv
import os

import github3


def make_parser():
    args = argparse.ArgumentParser(
        description='Convert GitHub issues to a CSV file'
    )
    # args.add_argument(
    #     '--fields', help='Names of data fields to take from an Issue'
    # )
    # args.add_argument(
    #     '--headers', help='Names of the column headers'
    # )
    args.add_argument(
        '--issue-state', help='Whether issues are closed, open, or both',
        choices=['open', 'closed', 'all'], default='all',
    )
    args.add_argument(
        '--output-file', help='Name of file to write the results to',
        default='gh2csv.csv',
    )
    args.add_argument(
        '--date-format', help='Way to format the dates when present',
        default='%m/%d/%Y',
    )
    args.add_argument(
        '--include-pull-requests',
        help='Toggles the inclusion of PRs in output',
        action='store_true', default=False,
    )
    args.add_argument(
        '--skip-date-normalization',
        help='By default, if a sequence of dates are not sequential, the tool '
             'will coerce them to look sequential.',
        action='store_true', default=False,
    )
    args.add_argument(
        'repository',
        help='Repository to retrieve issues from (e.g., rcbops/rpc-openstack)',
    )
    return args


def issues_for(owner, name, state, token):
    gh = github3.GitHub(token=token)
    repository = gh.repository(owner, name)
    return repository.issues(state=state, direction='asc')


def label_events_for(issue):
    if not issue.labels:
        return []
    return ((event.label['name'], event) for event in issue.events()
            if event.event == 'labeled')


def issue_to_dict(fields, issue):
    retrievers = fields_to_callables(fields)
    attributes = (retriever(issue) for retriever in retrievers)
    return collections.OrderedDict(
        (field, attr.encode('utf-8') if hasattr(attr, 'encode') else attr)
        for field, attr in zip(fields, attributes)
    )


def field_to_callable(field):
    attrs = field.split(':')
    if len(attrs) > 1 and attrs[0] == 'label':
        label_name = attrs[1]
        attribute = attrs[2]

        def retriever(issue):
            for label, event in label_events_for(issue):
                if label_name == label:
                    return getattr(event, attribute, None)
    else:
        def retriever(issue):
            return getattr(issue, field, None)

    return retriever


def fields_to_callables(fields):
    return [field_to_callable(field) for field in fields]


def format_dates(attributes, fmt):
    return [
        attr.strftime(fmt) if hasattr(attr, 'strftime') else attr
        for attr in attributes
    ]


def is_pull_request(issue):
    pr = issue.as_dict().get('pull_request')
    return pr and isinstance(pr, dict)


def normalize_sequential_dates(issue_list):
    """Adjust issue status dates based on latest state.

    issue_list must contain a contiguous set of items that represent dates.
    None is an exceptable date in this situation. These dates must start with
    created_at and finish with closed_at. This function searches issue_list for
    the dates and then modifies them such that older dates always preceed
    newer dates when viewed from created_at to closed_at.
    """
    start, finish = ('created_at', 'closed_at')
    date_fields = []
    for field in issue_list:
        if field == start:
            date_fields.append(field)
            continue
        elif not date_fields:
            continue
        else:
            date_fields.append(field)
        if field == finish:
            break

    number_of_dates = len(date_fields) - 1
    # We need to work backwards
    i = 0
    while i < number_of_dates:
        date = issue_list[date_fields[i]]
        filtered_dates = filter(None,
                                (issue_list[f] for f in date_fields[i + 1:]))
        if not filtered_dates:
            # If everything after this date is None, there's no need to keep
            # looping
            break
        next_earliest_date = min(filtered_dates)
        if date is not None and date > next_earliest_date:
            issue_list[date_fields[i]] = next_earliest_date
        i += 1

    return issue_list


def write_rows(filename, headers, fields, issues, date_format, include_prs,
               skip_normalization):
    with open(filename, 'w') as fd:
        writer = csv.writer(fd)
        writer.writerow(headers)
        for issue in issues:
            if not include_prs and is_pull_request(issue):
                continue
            issue_data = issue_to_dict(fields, issue)
            if not skip_normalization:
                issue_data = normalize_sequential_dates(issue_data)
            writer.writerow(format_dates(issue_data.values(), date_format))


def main():
    parser = make_parser()
    token = os.environ.get('GITHUB_TOKEN')
    if token is None:
        parser.exit(status=1,
                    message='No GITHUB_TOKEN specified by the user\n')
    args = parser.parse_args()

    repo_owner, repo_name = args.repository.split('/', 1)
    headers = [
        'ID', 'Link', 'Name', 'Backlog', 'Approved', 'Doing',
        'Needs Review', 'Dev Done'
    ]
    fields = [
        'number',
        'html_url',
        'title',
        'created_at',
        'label:status-approved:created_at',
        'label:status-doing:created_at',
        'label:status-needs-review:created_at',
        'closed_at',
    ]

    write_rows(
        filename=args.output_file,
        headers=headers,
        fields=fields,
        issues=issues_for(
            owner=repo_owner,
            name=repo_name,
            state=args.issue_state,
            token=token,
        ),
        date_format=args.date_format,
        include_prs=args.include_pull_requests,
        skip_normalization=args.skip_date_normalization,
    )
