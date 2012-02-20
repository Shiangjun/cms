#!/usr/bin/python
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2012 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from cms.db.SQLAlchemyAll import Evaluation
from cms.grading.TaskType import TaskType, \
     create_sandbox, delete_sandbox
from cms.util.Utils import get_compilation_command


class Batch(TaskType):
    """Task type class for a unique standalone submission source, with
    comparator (or not).

    Parameters needs to be a list, whose first element is 'diff',
    'comp' or 'grad', meaning that:
    - the user source is compiled alone and the output is checked with
      white diff if par[0] == 'diff';
    - the same with check done with a comparator if par[0] == 'comp';
    - the user source is compiled with the grader that also takes care
      of assigning the outcome if par[0] == 'grad'.

    In the first two cases, there is a second element which can be
    'file' or 'nofile', meaning that the io of the user program is
    done with 'input.txt' and 'output.txt' or with stdin and stdout.

    Note: a grader can read input.txt and res.txt (input and correct
    output) and should write to stdout the outcome and to stderr the
    explaination. It can also write to output.txt the output of the
    user function, but up to now is not needed.

    A comparator can read argv[1], argv[2], argv[3] (respectively,
    input, correct output and user output) and again should write the
    outcome to stdout and the text to stderr.

    """
    ALLOW_PARTIAL_SUBMISSION = False

    def compile(self):
        """See TaskType.compile.

        return (bool): success of operation.

        """
        # Detect the submission's language. The checks about the
        # formal correctedness of the submission are done in CWS,
        # before accepting it.
        language = self.submission.language

        # TODO: here we are sure that submission.files are the same as
        # task.submission_format. The following check shouldn't be
        # here, but in the definition of the task, since this actually
        # checks that task's task type and submission format agree.
        if len(self.submission.files) != 1:
            return self.finish_compilation(
                True, False, "Invalid files in submission",
                to_log="Submission contains %d files, expecting 1" %
                len(self.submission.files))

        # First and only one compilation.
        sandbox = create_sandbox(self)
        self.submission.compilation_sandbox = sandbox.path
        if "worker_shard" in self.__dict__:
            self.submission.compilation_shard = self.worker_shard
        files_to_get = {}
        format_filename = self.submission.files.keys()[0]
        source_filenames = [format_filename.replace("%l", language)]
        files_to_get[source_filenames[0]] = \
            self.submission.files[format_filename].digest
        # If a grader is specified, we add to the command line (and to
        # the files to get) the corresponding manager.
        if self.parameters[0] == "grad":
            source_filenames.append("grader.%s" % language)
            files_to_get[source_filenames[1]] = \
                self.submission.task.managers["grader.%s" % language].digest
        executable_filename = format_filename.replace(".%l", "")
        command = get_compilation_command(language,
                                          source_filenames,
                                          executable_filename)
        operation_success, compilation_success, text = self.compilation_step(
            sandbox,
            command,
            files_to_get,
            {executable_filename: "Executable %s for submission %s" %
             (executable_filename, self.submission.id)})
        delete_sandbox(sandbox)

        # We had only one compilation, hence we pipe directly its
        # result to the finalization.
        return self.finish_compilation(operation_success, compilation_success,
                                       text)

    def evaluate_testcase(self, test_number):
        sandbox = create_sandbox(self)
        self.submission.evaluations[test_number].evaluation_sandbox = \
            sandbox.path
        if "worker_shard" in self.__dict__:
            self.submission.evaluations[test_number].evaluation_shard = \
                self.worker_shard

        # First step: execute the contestant program. This is also the
        # final step if we have a grader, otherwise we need to run also
        # a white_diff or a comparator.
        command = ["./%s" % self.executable_filename]
        executables_to_get = {
            self.executable_filename:
            self.submission.executables[self.executable_filename].digest
            }
        files_to_get = {
            "input.txt": self.submission.task.testcases[test_number].input
            }
        allow_path = ["input.txt", "output.txt"]
        stdin_redirect = None
        stdout_redirect = None
        if self.parameters[0] == "grad":
            allow_path.append("res.txt")
            files_to_get["res.txt"] = \
                self.submission.task.testcases[test_number].output
        elif self.parameters[1] == "nofile":
            stdin_redirect = "input.txt"
            stdout_redirect = "output.txt"
        success, outcome, text = self.evaluation_step(
            sandbox,
            command,
            executables_to_get,
            files_to_get,
            self.submission.task.time_limit,
            self.submission.task.memory_limit,
            allow_path,
            stdin_redirect=stdin_redirect,
            stdout_redirect=stdout_redirect,
            final=(self.parameters[0] == "grad"))
        # If an error occur (our or contestant's), or we have a
        # grader, return immediately.
        if not success or outcome is not None or self.parameters[0] == "grad":
            delete_sandbox(sandbox)
            return self.finish_evaluation_testcase(test_number,
                                                   success, outcome, text)

        # Second step: diffing (manual or with manager).
        if self.parameters[0] == "diff":
            # We white_diff output.txt and res.txt.
            success, outcome, text = self.white_diff_step(
                sandbox,
                "output.txt", "res.txt",
                {"res.txt":
                 self.submission.task.testcases[test_number].output})
        elif self.parameters[0] == "comp":
            # Manager present: wonderful, it'll do all the job.
            manager_filename = self.submission.task.managers.keys()[0]
            success, outcome, text = self.evaluation_step(
                ["./%s" % manager_filename,
                 "input.txt", "res.txt", "output.txt"],
                {manager_filename:
                 self.submission.task.managers[manager_filename].digest},
                {"res.txt":
                 self.submission.task.testcases[test_number].output},
                allow_path=["input.txt", "res.txt", "output.txt"],
                final=True)

        # Whatever happened, we conclude.
        delete_sandbox(sandbox)
        return self.finish_evaluation_testcase(test_number,
                                               success, outcome, text)

    def evaluate(self):
        """See TaskType.evaluate.

        return (bool): success of operation.

        """
        if len(self.submission.executables) != 1:
            log_msg = "Submission contains %d executables, expecting 1" % \
                      len(self.submission.executables)
            return self.finish_evaluation(False, to_log=log_msg)

        self.executable_filename = self.submission.executables.keys()[0]

        for test_number in xrange(len(self.submission.evaluations),
                                  len(self.submission.task.testcases)):
            self.submission.get_session().add(
                Evaluation(text=None,
                           outcome=None,
                           num=test_number,
                           submission=self.submission))
        self.submission.evaluation_outcome = "ok"

        for test_number in xrange(len(self.submission.task.testcases)):
            success = self.evaluate_testcase(test_number)
            if not success:
                return self.finish_evaluation(False)
        return self.finish_evaluation(True)