#!/usr/bin/env python3

import argparse
import json
import logging
from sunyata.generate_api import get_deployer

class CLIDispatcher:

    operation_info={
        'create':{
            'help':'Create a new stack.',
            'initial':'c'
            },
        'deploy':{
            'help':'Deploy a new stack.',
            'initial':'d'
            },
        'examine':{
            'help':'Print the CF template that would be generated for this stack.'
            },
        'examine_deployed':{
            'help':'Print the CF template currently in use by this stack.'
            },
        'print_api_template':{
            'help':'Print the API configuration tha\'s the result of processing the template arguments you\'ve provided.',
            'initial':'p'
            }
        }

    def create(self, **kwargs):
        deployer = get_deployer(filenames=kwargs["templates"])
        deployer.deploy_initial()
        print(deployer.get_url())

    def deploy(self, **kwargs):
        deployer = get_deployer(filenames=kwargs["templates"])
        deployer.redeploy_to_stages(full_redeploy=kwargs["full_redeploy"])
        print(deployer.get_url())

    def examine(self, **kwargs):
        deployer = get_deployer(filenames=kwargs["templates"])
        body = deployer.get_template_from_config()
        print(deployer.stack_name)
        print(body)

    def examine_deployed(self, **kwargs):
        deployer = get_deployer(filenames=kwargs["templates"])
        body = deployer.get_template_from_cf()
        print(deployer.stack_name)
        print(body)

    def print_api_template(self, **kwargs):
        deployer = get_deployer(filenames=kwargs["templates"])
        body = deployer.get_template_from_cf()
        print(json.dumps(deployer.api, indent=2, sort_keys=True))

    def get_argument_parser(self):
        parser = argparse.ArgumentParser(description='Interact with sunyata from the command line.')
        operations = parser.add_mutually_exclusive_group(required=True)

        for operation in self.operation_info.keys():
            op = self.operation_info[operation]
            operation_cli = '--{0}'.format(operation.replace('_','-'))
            if op.get('initial', None):
                operations.add_argument('-{0}'.format(op['initial']), operation_cli, action='store_true', help='Operation: {0}'.format(op['help']))
            else:
                operations.add_argument(operation_cli, action='store_true', help='Operation: {0}'.format(op['help']))

        parser.add_argument('--template', dest="templates", required=True, nargs='+', help='Argument: The path to the sunyata template.  If used multiple times, the templates will be read in order and merged.  (That is, if a value is defined in the first template and then redefined in the second, the value in the second template will be the one used.)')
        parser.add_argument("-v", "--verbosity", dest="verbosity", action="count", default=0, help='Argument: Print random usually-useless information.  May or may not print anything depending on whether or not I\'ve implemented it yet, as I haven\'t right now.  Optional for all calls.  More repetitions equals more useless info, so -vv prints more than -v.')
        parser.add_argument("--full-redeploy", action='store_true', help='Argument: Fully redeploy the stack.  This is necessary to pick up changes in the supported paths, but will cause a brief outage.')
        return parser

    def handle_args(self, args):
        operation = None
        argdict = {}
        for pair in args._get_kwargs():
            if pair[0] in self.operation_info.keys():
                operation = operation if not pair[1] else pair[0]
            else:
                argdict[pair[0]] = pair[1]
        verbosity = argdict["verbosity"]
        if verbosity >= 2:
            logging.basicConfig(level=logging.DEBUG)
        elif verbosity >= 1:
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.WARN)
        getattr(self, operation)(**argdict)
#        try:
#            getattr(self, operation)(**argdict)
#        except Exception as e:
#            print(e.message)
#            exit(1)

    def do_stuff(self):
        parser = self.get_argument_parser()
        args = parser.parse_args()
        self.handle_args(args)
