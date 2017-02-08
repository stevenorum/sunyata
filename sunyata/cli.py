#!/usr/bin/env python

import argparse
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
            }
        }

    def create(self, **kwargs):
        deployer = get_deployer(filename=kwargs["template"])
        deployer.deploy_initial()
        print(deployer.get_url())

    def deploy(self, **kwargs):
        deployer = get_deployer(filename=kwargs["template"])
        deployer.redeploy_to_stages()
        print(deployer.get_url())

    def examine(self, **kwargs):
        deployer = get_deployer(filename=kwargs["template"])
        body = deployer.get_template_from_config()
        print(body)

    def examine_deployed(self, **kwargs):
        deployer = get_deployer(filename=kwargs["template"])
        body = deployer.get_template_from_cf()
        print(body)

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

        parser.add_argument('--template', required=True, help='Argument: The path to the sunyata template.')
        parser.add_argument("-v", "--verbosity", action="count", default=0, help='Argument: Print random usually-useless information.  May or may not print anything depending on whether or not I\'ve implemented it yet, as I haven\'t right now.  Optional for all calls.  More repetitions equals more useless info, so -vv prints more than -v.')

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
